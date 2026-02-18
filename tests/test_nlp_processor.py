import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from data_service.ai.nlp_processor import NlpProcessor
from data_service.ai.sources.base_source import Article

@pytest.fixture
def nlp_processor():
    # Patch get_config to avoid issues with missing config files
    with patch('data_service.ai.nlp_processor.get_config') as mock_config:
        mock_config.return_value.news_sources = {'ai_logic': {'use_llm_sentiment': False}}
        return NlpProcessor()

def test_preprocess(nlp_processor):
    text = "  <p>Hello WORLD!</p>  "
    expected = "hello world!"
    assert nlp_processor.preprocess(text) == expected

def test_extract_keywords(nlp_processor):
    text = "Gold prices are rising, but copper is falling. Interest rates and inflation are key."
    keywords = nlp_processor.extract_keywords(text)
    assert "gold" in keywords
    assert "copper" in keywords
    assert "interest rate" in keywords or "interest" in keywords
    # Based on our finance_vocab: "gold", "copper", "interest rate", "inflation"

def test_extract_entities(nlp_processor):
    # This might return empty if spaCy model is not loaded, but we have a fallback for commodities
    text = "Apple Inc. and Microsoft in New York. Gold is stable."
    entities = nlp_processor.extract_entities(text)
    assert "gold" in entities["COMMODITY"]
    # If spaCy is loaded, we'd check for ORG/GPE
    if nlp_processor.nlp:
        assert any(e in entities["ORG"] for e in ["Apple Inc.", "Microsoft"])

def test_analyze_flow(nlp_processor):
    article = Article(
        id="test_id",
        symbol="XAU",
        title="Gold rally expected",
        content="Bullish outlook for gold as inflation rises.",
        source="Test Source",
        published_at=datetime.now()
    )
    
    # Mock sentiment pipeline
    nlp_processor.sentiment_pipeline = MagicMock(return_value=[{'label': 'POSITIVE', 'score': 0.95}])
    
    analyzed = nlp_processor.analyze(article)
    assert analyzed.sentiment_score > 0.5
    assert "gold" in analyzed.raw_data["keywords"]
    assert "gold" in analyzed.raw_data["entities"]["COMMODITY"]
    assert "analysis_at" in analyzed.raw_data

@patch('data_service.ai.nlp_processor.OpenAI')
def test_gpt4_sentiment(mock_openai, nlp_processor):
    # Enable LLM sentiment
    nlp_processor.ai_config['use_llm_sentiment'] = True
    nlp_processor.openai_client = MagicMock()
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '{"sentiment": 0.75, "reasoning": "Positive outlook"}'
    nlp_processor.openai_client.chat.completions.create.return_value = mock_response
    
    score = nlp_processor.get_sentiment_llm("Title", "Content")
    assert score == 0.75
