import unittest
import sys
import os

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper import ForumScraper
from bs4 import BeautifulSoup

class TestForumScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = ForumScraper("https://example.com")

    def test_parse_number(self):
        """Test number parsing with K/M suffixes"""
        self.assertEqual(self.scraper.parse_number("123"), 123)
        self.assertEqual(self.scraper.parse_number("1.5K"), 1500)
        self.assertEqual(self.scraper.parse_number("2M"), 2000000)
        self.assertEqual(self.scraper.parse_number("0"), 0)
        self.assertEqual(self.scraper.parse_number("invalid"), 0)
        self.assertEqual(self.scraper.parse_number(None), 0)

    def test_parse_thread_element(self):
        """Test parsing a thread HTML element"""
        html = """
        <div class="structItem--thread">
            <div class="structItem-title">
                <a href="/threads/test-thread.12345/" data-tp-primary="on">Test Thread</a>
            </div>
            <a class="username">TestUser</a>
            <div class="structItem-cell--meta">
                <dl class="pairs pairs--justified">
                    <dt>Replies</dt>
                    <dd>10</dd>
                </dl>
                <dl class="pairs pairs--justified">
                    <dt>Views</dt>
                    <dd>100</dd>
                </dl>
            </div>
            <time class="structItem-startDate" datetime="2023-01-01T12:00:00+00:00">Jan 1, 2023</time>
        </div>
        """
        soup = BeautifulSoup(html, 'html.parser')
        element = soup.find('div')
        
        data = self.scraper.parse_thread_element(element, "https://example.com")
        
        self.assertIsNotNone(data)
        self.assertEqual(data['id'], '12345')
        self.assertEqual(data['title'], 'Test Thread')
        self.assertEqual(data['author'], 'TestUser')
        self.assertEqual(data['replies'], 10)
        self.assertEqual(data['views'], 100)
        self.assertEqual(data['url'], "https://example.com/threads/test-thread.12345/")

if __name__ == '__main__':
    unittest.main()
