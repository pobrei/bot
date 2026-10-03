import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import logging

logger = logging.getLogger(__name__)

class SentimentAnalyzer:
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer()
        # Cointelegraph RSS feed
        self.rss_url = "https://cointelegraph.com/rss"

    def get_market_sentiment(self, symbol: str) -> float:
        """
        Fetches the latest news headlines and calculates a sentiment score 
        between -1 (bearish) and 1 (bullish).
        If symbol is provided, filters or weighs news by symbol, 
        but for simplicity we just return a general market sentiment.
        """
        try:
            feed = feedparser.parse(self.rss_url)
            if not feed.entries:
                return 0.0
                
            asset_name = symbol.split('/')[0].lower() # e.g. 'btc'
            
            total_sentiment = 0.0
            relevant_articles = 0
            
            for entry in feed.entries[:20]: # Check last 20 headlines
                headline = entry.title.lower()
                # Simple keyword match
                if asset_name in headline or "crypto" in headline or "market" in headline:
                    score = self.analyzer.polarity_scores(headline)
                    total_sentiment += score['compound']
                    relevant_articles += 1
            
            if relevant_articles > 0:
                avg_sentiment = total_sentiment / relevant_articles
                logger.info(f"Sentiment for {symbol} (based on {relevant_articles} articles): {avg_sentiment:.3f}")
                return avg_sentiment
            else:
                return 0.0
                
        except Exception as e:
            logger.error(f"Error fetching sentiment: {e}")
            return 0.0
