import unittest
from evaluation.run import rows_equal, failure_category

class ScoringTest(unittest.TestCase):
    def test_aliases_order_and_rounding(self):
        self.assertTrue(rows_equal([{'金额':100.001,'地区':'华东'}],[['华东',100.00]],.011)[0])
    def test_different_amount_rejected_even_at_large_scale(self):
        self.assertFalse(rows_equal([{'金额':15658627.69}],[[15658626.69]],.011)[0])
    def test_ratio_precision(self):
        self.assertFalse(rows_equal([{'比率':.12}],[[.1234]],.011)[0])
        self.assertTrue(rows_equal([{'比率':.123400001}],[[.1234]],.011)[0])
    def test_duplicates_and_extra_columns_rejected(self):
        self.assertFalse(rows_equal([{'x':1},{'x':1}],[[1],[2]],.011)[0])
        self.assertFalse(rows_equal([{'x':1,'y':2}],[[1]],.011)[0])
    def test_empty_result(self):
        self.assertTrue(rows_equal([],[],.011)[0])
        self.assertFalse(rows_equal([{'总额':None}],[],.011)[0])
    def test_sales_numbers_are_not_http_errors(self):
        self.assertNotEqual(failure_category({'analysis':'销售额 4292183.75','status':'completed','sql':'SELECT 1'},'值不匹配'),'infrastructure')
        self.assertEqual(failure_category({'analysis':'模型接口返回 HTTP 429','status':'failed'},'失败'),'infrastructure')
        self.assertEqual(failure_category({'analysis':'流式响应失败 HTTP Error 400: Bad Request','status':'failed'},'失败'),'infrastructure')

if __name__=='__main__':unittest.main()
