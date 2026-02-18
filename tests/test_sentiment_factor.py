import pytest
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from data_service.ai.sentiment_factor import SentimentFactor
from data_service.ai.sources.base_source import Article
from data_service.storage.database_manager import DatabaseManager

@pytest.fixture
def mock_db():
    db = MagicMock(spec=DatabaseManager)
    db.get_recent_articles.return_value = []
    db.get_latest_sentiment_factors.return_value = None
    return db

@pytest.fixture
def sentiment_factor(mock_db):
    with patch('data_service.ai.sentiment_factor.get_config') as mock_cfg:
        mock_cfg.return_value.news_sources = {
            'processing_settings': {'sentiment_recency_half_life_hours': 2.0}
        }
        sf = SentimentFactor(db_manager=mock_db)
        return sf

def test_decay_weight(sentiment_factor):
    # Now
    w_now = sentiment_factor.calculate_decay_weight(datetime.now())
    assert math.isclose(w_now, 1.0, rel_tol=0.01)
    
    # 2 hours ago (half-life)
    w_2h = sentiment_factor.calculate_decay_weight(datetime.now() - timedelta(hours=2))
    assert math.isclose(w_2h, 0.5, rel_tol=0.01)
    
    # 4 hours ago (two half-lives)
    w_4h = sentiment_factor.calculate_decay_weight(datetime.now() - timedelta(hours=4))
    assert math.isclose(w_4h, 0.25, rel_tol=0.01)

def test_compute_factors_weighted_mean(sentiment_factor, mock_db):
    now = datetime.now()
    articles = [
        Article(id="1", symbol="XAU", title="T1", content="C1", source="telegram", 
                published_at=now, sentiment_score=0.8),
        Article(id="2", symbol="XAU", title="T2", content="C2", source="google_rss", 
                published_at=now - timedelta(hours=2), sentiment_score=0.4)
    ]
    mock_db.get_recent_articles.return_value = articles
    
    factors = sentiment_factor.compute_factors("XAU")
    
    # Calculations:
    # Art 1: Score 0.8, Source Weight 1.2, Recency 1.0 -> Weighted Sum = 0.8 * 1.2 * 1.0 = 0.96, Weight = 1.2
    # Art 2: Score 0.4, Source Weight 0.8, Recency 0.5 -> Weighted Sum = 0.4 * 0.8 * 0.5 = 0.16, Weight = 0.4
    # Total Weight = 1.6, Total Weighted Sum = 1.12
    # Mean = 1.12 / 1.6 = 0.7
    
    assert math.isclose(factors['sentiment_level'], 0.7, rel_tol=0.01)
    assert factors['sentiment_variance'] > 0 # Should have some variance
    
    # Verify DB calls
    mock_db.save_sentiment_snapshot.assert_called_once()

def test_momentum_calculation(sentiment_factor, mock_db):
    mock_db.get_recent_articles.return_value = [
        Article(id="1", symbol="XAU", title="T1", content="C1", source="investing.com", 
                published_at=datetime.now(), sentiment_score=0.6)
    ]
    
    # Mock past factors from 6h ago
    mock_db.get_latest_sentiment_factors.return_value = {
        "sentiment_level": 0.2
    }
    
    factors = sentiment_factor.compute_factors("XAU")
    
    # New level is 0.6 (since only one article at 'now')
    # Momentum = 0.6 - 0.2 = 0.4
    assert math.isclose(factors['sentiment_momentum'], 0.4, rel_tol=0.01)

def test_ingest_flow(sentiment_factor, mock_db):
    articles = [
        Article(id="1", symbol="XAU", title="T1", content="C1", source="telegram", 
                published_at=datetime.now(), sentiment_score=0.5),
        Article(id="2", symbol="TSLA", title="T2", content="C2", source="telegram", 
                published_at=datetime.now(), sentiment_score=-0.5)
    ]
    
    sentiment_factor.ingest(articles)
    
    # Verify each article saved
    assert mock_db.save_article.call_count == 2
    # Verify factors computed for both symbols
    assert mock_db.get_recent_articles.call_count == 2
