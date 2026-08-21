"""strainer.jp の大株主セルの読み取り。

2026-08-21: 手間いらず(2477)の大株主が1名しか出ていなかった。
原因はセルの連結文字列を「カンマの後ろ3桁が株数の下位」という規則で割っていたこと。
株数が千株未満（＝カンマが付かない）行を軒並み捨てていた。
大型株はカンマが付くので通っており、**小型株ほど株主が消える**壊れ方だった。

セルは実際には2段に分かれている:
    <td><span><div><a>580</a></div><div>9.23%</div></span></td>
get_text() で読むと "5809.23%" となり境界が消える。要素から読むこと。
"""

import os
import sys
import unittest

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jp_company_scraper import _split_shares_ratio


def cell(html):
    return BeautifulSoup(f'<td>{html}</td>', 'html.parser').find('td')


class TestSplitSharesRatio(unittest.TestCase):
    def test_small_holding_without_comma(self):
        """カンマの無い3桁の株数。以前はこの行がまるごと捨てられていた。"""
        shares, ratio = _split_shares_ratio(
            cell('<span><div><a>580</a></div><div>9.23%</div></span>'))
        self.assertEqual(ratio, 9.23)
        self.assertEqual(shares, 580_000)

    def test_large_holding_with_comma(self):
        shares, ratio = _split_shares_ratio(
            cell('<span><div><a>3,290</a></div><div>52.4%</div></span>'))
        self.assertEqual(ratio, 52.4)
        self.assertEqual(shares, 3_290_000)

    def test_ratio_under_one_percent(self):
        shares, ratio = _split_shares_ratio(
            cell('<span><div><a>12</a></div><div>0.35%</div></span>'))
        self.assertEqual(ratio, 0.35)
        self.assertEqual(shares, 12_000)

    def test_hundred_percent(self):
        _, ratio = _split_shares_ratio(
            cell('<span><div><a>1,000</a></div><div>100%</div></span>'))
        self.assertEqual(ratio, 100.0)

    def test_ratio_only(self):
        """株数が無くても比率が取れれば大株主表は成立する。"""
        shares, ratio = _split_shares_ratio(cell('<div>7.32%</div>'))
        self.assertEqual(ratio, 7.32)
        self.assertIsNone(shares)

    def test_no_ratio(self):
        self.assertEqual(_split_shares_ratio(cell('<div>580</div>')), (None, None))

    def test_empty(self):
        self.assertEqual(_split_shares_ratio(cell('')), (None, None))

    def test_garbage_ratio_does_not_raise(self):
        self.assertEqual(_split_shares_ratio(cell('<div>あ%</div>')), (None, None))

    def test_concatenated_text_is_not_used(self):
        """get_text() で読むと 5809.23% になる。そこから 580 と 9.23 を
        復元できないので、要素で分けていることを確かめる。"""
        c = cell('<span><div><a>580</a></div><div>9.23%</div></span>')
        self.assertEqual(c.get_text(strip=True), '5809.23%')   # 連結されている
        self.assertEqual(_split_shares_ratio(c), (580_000, 9.23))


if __name__ == '__main__':
    unittest.main()
