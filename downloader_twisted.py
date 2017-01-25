from __future__ import absolute_import
import logging
import os
from os.path import join, basename, splitext
from urlparse import urljoin
import zipfile
from lxml import etree
from twisted.internet import defer, reactor, threads
from twisted.persisted import dirdbm
from twisted.python import log
from twisted.python.failure import Failure
from eronet import Map, Album
from eronet.twisted import get_page, download_page
from eronet.utils import get_query, zip_index, zip_date, strip, first


logger = logging.getLogger('eronet')


class PageSpider(object):
    """Follows html pages and extracts album info"""

    def __init__(self, settings):
        self.start_url = settings['start_url']
        self.proxy_url = settings.get('proxy_url')
        self.dbm = dirdbm.Shelf(settings['dbm_dir'])
        self.delay = settings['download_delay']
        self.user_agent = settings['user_agent']
        self.albums = []

    def start(self):
        """Starts crawling self.start_url

        Returns:
            New albums that should be downloaded (as Deferred)
        """
        self.albums = []
        self.finished = defer.Deferred()
        self.download_page(self.start_url)
        return self.finished

    def download_page(self, url):
        def on_error(failure):
            log.err(failure)
            self.finished.callback(self.albums)

        #d = getPage(url)
        d = get_page(url, self.proxy_url, agent=self.user_agent)
        d.addCallback(self.parse_page, url)
        d.addCallback(self.collect_albums)
        d.addCallback(self.download_next_page)
        d.addErrback(on_error)

    def parse_page(self, html, url):
        """Returns not downloaded albums and next page url"""

        logger.info('Got html for %s (%d bytes)', url, len(html))

        not_downloaded = lambda url: zip_index(url) not in self.dbm.get(zip_date(url), [])

        doc = etree.fromstring(html, etree.HTMLParser())
        all_zip_urls = map(strip, doc.xpath('//a[text()="Zip"]/@href'))
        zip_urls_to_download = filter(not_downloaded, all_zip_urls)

        albums = []
        #for link in zip_urls_to_download:
        for td in doc.xpath('//a[text()="Zip"]/..'):
            zip_url = first(td.xpath('./a[text()="Zip"]/@href')).strip()
            title = first(td.xpath('./a/img/@alt'))
            external_url = first(td.xpath('./a[@onmouseover]/@href'))
            external_url = get_query(external_url, unquote=False).get('url', '')

            if zip_url in zip_urls_to_download:
                album = Album(zip_url, title, external_url)
                if album.valid:
                    albums.append(album)
                else:
                    logger.warning("Album not valid: %s", album)

        next_page_url = None
        if set(all_zip_urls) == set(zip_urls_to_download):
            link = doc.xpath('//a[starts-with(text(), "NEXT")]/@href')
            if link:
                next_page_url = urljoin(url, link[0])

        return albums, next_page_url

    def collect_albums(self, page_data):
        albums, _ = page_data

        self.albums.extend(albums)

        return page_data

    def download_next_page(self, page_data):
        _, next_page_url = page_data
        if next_page_url:
            if self.delay:
                reactor.callLater(self.delay, self.download_page, next_page_url)
            else:
                self.download_page(next_page_url)
        else:
            self.finished.callback(self.albums)


class ZipDownloader(object):
    """Downloads zip archives and extracts them"""

    def __init__(self, albums, settings):
        self.albums = albums
        self.download_dir = settings['download_dir']
        self.dbm = dirdbm.Shelf(settings['dbm_dir'])
        self.concurrency = settings['downloader_concurrent_requests']
        self.delay = settings['download_delay']
        self.proxy_url = settings.get('proxy_url')
        self.user_agent = settings['user_agent']

    @defer.inlineCallbacks
    def start(self):
        """Starts downloading all zip archives. Returns all downloaded albums as Deferred."""
        task = defer.Deferred()
        task.addCallback(self.get_album)
        results = yield Map(task, self.albums, self.concurrency, self.delay)

        albums = [r for r in results if not isinstance(r, Failure)]
        failures = [r for r in results if isinstance(r, Failure)]

        if failures:
            logger.error('Not all zip archives were downloaded:')
            for f in failures:
                logger.error(f.getErrorMessage())

        defer.returnValue(albums)

    @defer.inlineCallbacks
    def get_album(self, album):
        """Downloads and unpacks album"""
        zip_url = album.zip_url
        zip_filename = join(self.download_dir, basename(zip_url))

        try:
            #yield downloadPage(zip_url, zip_filename)
            yield download_page(zip_url, zip_filename, self.proxy_url, agent=self.user_agent)
        except Exception as e:
            raise Exception("Can't download {0}: {1}".format(album, e))

        logger.info('%s received, unzipping', basename(zip_filename))
        try:
            folder = yield threads.deferToThread(self.unzip, zip_filename)
            album.folder = folder
        except Exception as e:
            raise Exception("Can't unzip {0} ({1}): {2}".format(zip_filename, album, e))

        try:
            yield self.remove_zip_file(folder, zip_filename)
        except Exception as e:
            raise Exception("Can't remove {0} ({1}): {2}".format(zip_filename, album, e))

        yield self.mark_as_downloaded(zip_filename)

        defer.returnValue(album)

    def unzip(self, zip_filename):
        def get_folder(url_or_zipfile):
            name = splitext(basename(url_or_zipfile))[0]
            return join(self.download_dir, *name.split('_'))

        folder = get_folder(zip_filename)
        z = zipfile.ZipFile(zip_filename, "r")
        try:
            z.extractall(folder)
        finally:
            z.close()

        return folder

    def remove_zip_file(self, folder, zip_filename):
        logger.info('%s extracted to %s, removing zip', basename(zip_filename), folder)
        os.remove(zip_filename)
        return zip_filename

    def mark_as_downloaded(self, zip_filename):
        key = zip_date(zip_filename)
        value = self.dbm.get(key, [])
        value.append(zip_index(zip_filename))
        self.dbm[key] = value
