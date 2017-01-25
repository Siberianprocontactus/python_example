from datetime import datetime, timedelta
from classified_stats.utils import (parse_natural_date, change_query, index_of_similar, months_fr,
                                    parse_ouedkniss_date)


def test_parse_natural_date():
    now = datetime.now()
    assert (parse_natural_date('15 sec ago', now) ==
            now - timedelta(seconds=15))
    assert (parse_natural_date('4 min 23 sec ago', now) ==
            now - timedelta(seconds=23, minutes=4))
    assert (parse_natural_date('2 hours 2 min ago', now) ==
            now - timedelta(hours=2, minutes=2))
    assert (parse_natural_date('1 hour 36 min ago', now) ==
            now - timedelta(hours=1, minutes=36))
    assert (parse_natural_date('1 day 15 hours 36 min ago', now) ==
            now - timedelta(days=1, hours=15, minutes=36))
    assert (parse_natural_date('4 days 1 hour 1 min ago', now) ==
            now - timedelta(days=4, hours=1, minutes=1))
    assert (parse_natural_date('1 week 7 hours ago', now) ==
            now - timedelta(weeks=1, hours=7))
    assert (parse_natural_date('2 weeks 9 hours 13 min ago', now) ==
            now - timedelta(weeks=2, hours=9, minutes=13))

def test_change_query():
    assert change_query('http://www.1808080.com/category.php?cat=kuwait_car&page=2',
                        dict(one=1)) ==\
           'http://www.1808080.com/category.php?one=1'

    assert change_query('http://www.1808080.com/category.php?cat=kuwait_car&page=2',
                        page=3) ==\
           'http://www.1808080.com/category.php?cat=kuwait_car&page=3'

    assert change_query('http://www.1808080.com/category.php?cat=kuwait_car&page=2',
                        {}) ==\
           'http://www.1808080.com/category.php'

def test_get_index_of_similar():
    assert index_of_similar('aout', months_fr) == 7
    assert index_of_similar('dec', months_fr) == 11
    assert index_of_similar('fev', months_fr) == 1

def test_parse_ouedkniss_date():
    assert parse_ouedkniss_date('15-Aout-2013') == datetime(2013, 8, 15)
