"""Prompt templates for the analysis pipeline."""

from __future__ import annotations

SYSTEM_ANALYST = """You are a news analysis system specializing in monitoring coverage related to Israel, Israeli security agencies, the IDF, Zionism, and Jewish communities worldwide. You analyze articles for reputational risk and classify content accurately. You respond only in valid JSON format. You distinguish between legitimate policy criticism and hostile framing, antisemitic content, misinformation, and incitement."""

CLASSIFICATION_PROMPT = """Analyze this news article and provide scores and labels.

Article URL: {url}
Source: {source_name}
Source orientation: {orientation}
Source credibility: {credibility_level}
Title: {title}
Text:
{body_text}

Score each criterion from 0 to 10:
- israel_political_relevance: How relevant is this to Israel, Israeli government, IDF, Israeli security agencies, Israeli defense/cyber companies, Jewish communities, or Zionism?
- antisemitic_content: Does this contain antisemitic tropes, rhetoric, or framing?
- anti_zionist_content: Does this delegitimize Zionism or the right of Israel to exist as a concept?
- misinformation_risk: Does this contain unverified claims, misleading framing, or factual errors about Israel/IDF/Israeli agencies?
- war_crimes_accusation: Does this contain accusations of war crimes or crimes against humanity against Israel/IDF?
- sanctions_or_boycott_risk: Does this relate to BDS, sanctions, or boycott actions against Israel?
- un_or_legal_action: Does this relate to UN resolutions, ICC/ICJ proceedings, or legal actions against Israel?
- hostile_media_narrative: Is this a hostile media narrative that delegitimizes Israel, IDF, Zionism, or Jewish communities beyond ordinary policy criticism?
- diplomatic_incident: Does this relate to a diplomatic crisis or incident involving Israel?

Also assign applicable content labels from this list:
- legitimate_policy_criticism
- anti_israel_framing
- anti_zionist_framing
- antisemitic_content
- misinformation_or_unverified_claim
- reputational_crisis
- incitement_or_threat

Respond with ONLY this JSON structure:
{{
  "scores": {{
    "israel_political_relevance": 0,
    "antisemitic_content": 0,
    "anti_zionist_content": 0,
    "misinformation_risk": 0,
    "war_crimes_accusation": 0,
    "sanctions_or_boycott_risk": 0,
    "un_or_legal_action": 0,
    "hostile_media_narrative": 0,
    "diplomatic_incident": 0
  }},
  "labels": [],
  "confidence": 0.0,
  "rationale": ""
}}"""

CLAIM_EXTRACTION_PROMPT = """Extract key reputational-risk claims from this article.

Article URL: {url}
Source: {source_name}
Title: {title}
Text:
{body_text}

For each claim that creates reputational risk to Israel, the IDF, Israeli security agencies, Zionism, or Jewish communities, extract:
- claim_text: the exact or near-exact claim from the article
- category: one of [war_crime, human_rights_violation, international_law_violation, misinformation, conspiracy, incitement, diplomatic_failure, policy_criticism, antisemitism, boycott_action, legal_proceeding]
- target_entity: the entity being accused or targeted (e.g., "Israel", "IDF", "Mossad", "Israeli government")
- status: one of [verified, disputed, unsupported, false, needs_human_verification]
- confidence: your confidence in the status assessment (0.0 to 1.0)

Clearly separate:
- What the article/source claims
- What you can infer from context
- What is verified vs disputed vs unsupported

Respond with ONLY this JSON:
{{
  "claims": [
    {{
      "claim_text": "",
      "category": "",
      "target_entity": "",
      "status": "needs_human_verification",
      "confidence": 0.0
    }}
  ]
}}"""

TOPIC_GROUPING_PROMPT = """Group these articles into topics (same underlying event or narrative).

Articles:
{articles_json}

Existing active topics:
{existing_topics_json}

For each group, provide:
- topic_name: short descriptive name for the event/narrative
- summary: 1-2 sentence summary
- article_indices: which articles (by index) belong to this topic
- existing_topic_id: if this matches an existing topic, provide its ID (or null for new)
- lifecycle: one of [emerging, growing, viral, declining, dormant, resurfacing]
- labels: applicable content labels

Respond with ONLY this JSON:
{{
  "topics": [
    {{
      "topic_name": "",
      "summary": "",
      "article_indices": [],
      "existing_topic_id": null,
      "lifecycle": "emerging",
      "labels": []
    }}
  ]
}}"""

SOURCE_COMPARISON_PROMPT = """Compare how different sources cover this topic.

Topic: {topic_name}
Topic summary: {topic_summary}

Source coverage:
{source_coverage_json}

Provide a brief comparison of how different sources frame this story. Note any significant differences in framing, emphasis, or claims. Identify if any source is missing key context or presenting misleading framing.

Respond with ONLY this JSON:
{{
  "comparison_summary": "",
  "framing_differences": [],
  "missing_context": [],
  "notable_bias": []
}}"""

REPORT_SECTION_PROMPT = """Write a brief factual report section for this topic.

Topic: {topic_name}
Severity: {severity}
Lifecycle: {lifecycle}
Final score: {final_score}
Labels: {labels}

Summary: {summary}

Source comparison: {source_comparison}

Claims:
{claims_json}

Write the following sections in neutral, factual style:
1. "why_it_matters": 1-2 sentences on why this matters for reputational risk awareness
2. "recommended_response": Only if misinformation risk is high. Short factual talking points with citations. No harassment, brigading, deception, or manipulation. Include uncertainty where appropriate.

Respond with ONLY this JSON:
{{
  "why_it_matters": "",
  "recommended_response": ""
}}"""
