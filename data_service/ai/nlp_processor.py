import logging
import re
import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

import torch
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
import spacy
from openai import OpenAI

from data_service.ai.sources.base_source import Article
from data_service.utils.config_loader import get_config

logger = logging.getLogger(__name__)

class NlpProcessor:
    def __init__(self):
        self.config = get_config()
        self.ai_config = self.config.news_sources.get('ai_logic', {})

        self.device = 0 if torch.cuda.is_available() else -1
        self._load_models()

        # Load spaCy for NER
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning("spaCy model 'en_core_web_sm' not found. Entity recognition will be limited.")
            self.nlp = None

        # Finance Keywords for extraction
        self.finance_vocab = set([
            "gold", "silver", "copper", "platinum", "xau", "xag", "hg",
            "bullish", "bearish", "inflation", "fed", "interest", "rate", "rates",
            "tsla", "nvda", "aapl", "googl", "stock", "perp", "liquidity",
            "fomc", "powell", "deficit", "yield", "recession", "rally"
        ])

        # LLM Client (OpenAI)
        self.openai_client = None
        if os.getenv("OPENAI_API_KEY"):
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _load_models(self):
        """Load FinBERT as primary, SST-2 as fallback."""
        self.finbert_pipeline = None

        try:
            tokenizer = AutoTokenizer.from_pretrained('ProsusAI/finbert')
            model = AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert')
            self.finbert_pipeline = pipeline(
                "sentiment-analysis",
                model=model,
                tokenizer=tokenizer,
                device=self.device,
                top_k=3  # Return all 3 class probabilities
            )
            logger.info("Loaded ProsusAI/finbert (finance-specific sentiment model)")
        except Exception as e:
            logger.warning(f"FinBERT unavailable, falling back to SST-2: {e}")
            self.finbert_pipeline = None

        # SST-2 as fallback (always available, cached locally)
        self.sst2_pipeline = pipeline(
            "sentiment-analysis",
            model="distilbert-base-uncased-finetuned-sst-2-english",
            device=self.device
        )
        logger.info("Loaded SST-2 fallback model")

    def preprocess(self, text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.lower()
        text = ' '.join(text.split())
        return text

    def get_sentiment_finbert(self, text: str) -> float:
        """
        Get sentiment using ProsusAI/finbert (3-class: positive/negative/neutral).
        Returns weighted score: P(positive) - P(negative), range [-1, 1].
        This produces calibrated scores that reflect confidence across all classes.
        """
        truncated_text = text[:510]  # BERT token limit
        results = self.finbert_pipeline(truncated_text)[0]

        # results is a list of dicts with label/score for all 3 classes
        probs = {r['label']: r['score'] for r in results}
        score = probs.get('positive', 0.0) - probs.get('negative', 0.0)
        return round(score, 4)

    def get_sentiment_sst2(self, text: str) -> float:
        """Get sentiment using SST-2 fallback (binary: POSITIVE/NEGATIVE)."""
        truncated_text = text[:1500]
        result = self.sst2_pipeline(truncated_text)[0]
        score = result['score']
        return -score if result['label'] == 'NEGATIVE' else score

    def get_sentiment_hf(self, text: str) -> float:
        """Route to FinBERT if available, otherwise SST-2."""
        if self.finbert_pipeline:
            return self.get_sentiment_finbert(text)
        return self.get_sentiment_sst2(text)

    def get_sentiment_llm(self, title: str, content: str) -> Optional[float]:
        """Get sentiment using GPT-4."""
        if not self.openai_client or not self.ai_config.get('use_llm_sentiment', False):
            return None

        try:
            prompt = f"""
            Analyze the sentiment of the following financial news article for trading purposes.
            Title: {title}
            Content: {content[:1000]}

            Return a JSON object with:
            - "sentiment": float between -1.0 (very bearish) and 1.0 (very bullish)
            - "reasoning": short string explaining why
            """

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )

            data = json.loads(response.choices[0].message.content)
            return float(data.get("sentiment", 0.0))
        except Exception as e:
            logger.error(f"GPT-4 sentiment analysis failed: {e}")
            return None

    def extract_keywords(self, text: str) -> List[str]:
        """Extract top finance-relevant terms."""
        text_lower = text.lower()
        found = []
        for word_or_phrase in self.finance_vocab:
            if word_or_phrase in text_lower:
                if re.search(r'\b' + re.escape(word_or_phrase) + r'\b', text_lower):
                    found.append(word_or_phrase)
        return found

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extract ORG, GPE, and PERSON entities using spaCy."""
        entities = {"ORG": [], "GPE": [], "PERSON": [], "COMMODITY": []}
        if not self.nlp:
            return entities

        doc = self.nlp(text[:10000])
        for ent in doc.ents:
            if ent.label_ in entities:
                entities[ent.label_].append(ent.text)

        # Custom logic for commodities if not caught by NER
        for word in ["gold", "silver", "copper", "oil", "platinum"]:
            if word in text.lower():
                entities["COMMODITY"].append(word)

        # Clean duplicates
        for key in entities:
            entities[key] = list(set(entities[key]))

        return entities

    def analyze(self, article: Article) -> Article:
        """Run the full NLP pipeline on an article."""
        clean_title = self.preprocess(article.title)
        clean_content = self.preprocess(article.content)
        full_text = f"{clean_title}. {clean_content}"

        # 1. Sentiment: LLM first (if configured), then FinBERT/SST-2
        sentiment = self.get_sentiment_llm(article.title, article.content)
        if sentiment is None:
            sentiment = self.get_sentiment_hf(full_text)

        # 2. Keywords & Entities
        keywords = self.extract_keywords(full_text)
        entities = self.extract_entities(full_text)

        article.sentiment_score = sentiment
        article.raw_data = article.raw_data or {}
        article.raw_data.update({
            "keywords": keywords,
            "entities": entities,
            "analysis_at": datetime.now().isoformat(),
            "model": "finbert" if self.finbert_pipeline else "sst2"
        })

        logger.debug(f"Analyzed article: {article.title} | Score: {sentiment:.2f}")
        return article

    def analyze_batch(self, articles: List[Article], batch_size: int = 8) -> List[Article]:
        """
        Batch process articles for efficiency. Uses transformer batching
        to reduce overhead while keeping memory usage bounded.

        Args:
            articles: List of articles to analyze
            batch_size: Max articles per batch (default 8, safe for ~2GB RAM)

        Returns:
            List of analyzed articles
        """
        if not articles:
            return []

        analyzed = []
        total = len(articles)

        # Process in batches
        for i in range(0, total, batch_size):
            batch = articles[i:i + batch_size]
            batch_texts = []

            # Prepare texts for batch sentiment
            for art in batch:
                clean_title = self.preprocess(art.title)
                clean_content = self.preprocess(art.content)
                full_text = f"{clean_title}. {clean_content}"
                batch_texts.append(full_text)

            # Batch sentiment analysis (much faster than one-by-one)
            sentiments = self._batch_sentiment(batch_texts)

            # Apply results and extract keywords/entities
            for j, art in enumerate(batch):
                art.sentiment_score = sentiments[j]

                # Keywords/entities (fast, no need to batch)
                full_text = batch_texts[j]
                keywords = self.extract_keywords(full_text)
                # Skip entity extraction for speed - it's expensive and rarely used
                # entities = self.extract_entities(full_text)

                art.raw_data = art.raw_data or {}
                art.raw_data.update({
                    "keywords": keywords,
                    "analysis_at": datetime.now().isoformat(),
                    "model": "finbert" if self.finbert_pipeline else "sst2"
                })

                # Clear content to free memory (keep title for logging)
                art.content = None
                analyzed.append(art)

            logger.debug(f"Batch {i//batch_size + 1}: processed {len(batch)} articles")

        logger.info(f"Batch NLP complete: {total} articles processed")
        return analyzed

    def _batch_sentiment(self, texts: List[str]) -> List[float]:
        """
        Get sentiment scores for a batch of texts efficiently.
        Uses the transformer pipeline's built-in batching.
        """
        if not texts:
            return []

        # Truncate texts for BERT (max 512 tokens ~ 510 chars to be safe)
        truncated = [t[:510] for t in texts]

        try:
            if self.finbert_pipeline:
                # FinBERT batch processing
                results = self.finbert_pipeline(truncated, batch_size=len(truncated))
                scores = []
                for result in results:
                    # result is list of dicts with label/score for all 3 classes
                    probs = {r['label']: r['score'] for r in result}
                    score = probs.get('positive', 0.0) - probs.get('negative', 0.0)
                    scores.append(round(score, 4))
                return scores
            else:
                # SST-2 fallback batch processing
                results = self.sst2_pipeline(truncated, batch_size=len(truncated))
                scores = []
                for result in results:
                    score = result['score']
                    if result['label'] == 'NEGATIVE':
                        score = -score
                    scores.append(round(score, 4))
                return scores
        except Exception as e:
            logger.error(f"Batch sentiment failed, falling back to sequential: {e}")
            # Fallback to sequential processing
            return [self.get_sentiment_hf(t) for t in texts]
