# -*- coding: utf-8 -*-
import tempfile
from flask import Flask, render_template, jsonify, Response, make_response

from scraping import list_jobs
from storage import db, category_as_csv, category_images_as_zip

from . import tasks


app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/products/<category_node>')
def product_list(category_node):
    products = []

    category = db.categories.find_one({'_id': category_node})
    if category:
        descendant_categories = db.categories.find({'path': {'$regex': '^{c[path]}{c[_id]},'.format(c=category)}})
        category_ids = [category['_id']] + [c['_id'] for c in descendant_categories]
        #print category_ids

        products = list(db.products.find({'$or': [{'categories._id': {'$in': category_ids}},
                                                  {'scraped_category': {'$in': category_ids}}]}))
        for i, p in enumerate(products):
            p.pop('_id')
            p['index'] = i + 1
            p['price'] = float(p['price'].lstrip('$')) if p.get('price') else None

    return jsonify(products=products)


@app.route('/scrape/<category_node>')
def scrape(category_node):
    tasks.scrape.delay(category_node)
    return jsonify({})


@app.route('/categories')
def category_tree():
    #import codecs
    #import json

    #with codecs.open('category_tree.json', 'r', 'utf-8') as f:
    #    return jsonify(tree=json.load(f))

    doc = db.categories.find_one({'_id': 'tree'})
    if doc:
        return jsonify(tree=doc['categories'])
    else:
        return jsonify({})


@app.route('/jobs')
def job_list():
    return jsonify(jobs=list_jobs())


@app.route('/jobs', methods=['DELETE'])
def delete_jobs():
    db.jobs.remove({'status': 'finished'})
    return ''


@app.route('/export/csv/<cid>')
def csv(cid):
    return Response(category_as_csv(cid, tempfile.TemporaryFile()),
                    mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename={}.csv".format(cid)})


@app.route('/export/images/<cid>')
def zip(cid):
    return Response(category_images_as_zip(cid, tempfile.TemporaryFile()),
                    mimetype='application/zip',
                    headers={"Content-Disposition": "attachment;filename={}-images.zip".format(cid)})
