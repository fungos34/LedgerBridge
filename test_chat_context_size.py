#!/usr/bin/env python
"""Test script to show the actual context size being sent to the AI chatbot."""

from pathlib import Path

# Simulate loading context
ai_context_dir = Path(__file__).parent / "src" / "paperless_firefly" / "review" / "web" / "ai_context"
context_file = ai_context_dir / "SPARKLINK_AI_CONTEXT.txt"

if context_file.exists():
    full_context = context_file.read_text(encoding="utf-8")
    print(f"Full AI context file size: {len(full_context)} chars ({len(full_context) / 1024:.1f} KB)")
    print(f"Lines: {len(full_context.splitlines())}")
    print()
    
    # Simulate page-specific extraction for unified-review page
    sections_to_include = [
        "## APPLICATION IDENTITY",
        "## EXACT WORKFLOW",
        "## MAIN NAVIGATION",
        "## REVIEW & MATCH & IMPORT PAGE",
        "## AI ASSISTANT FEATURES",
        "## COMMON USER QUESTIONS",
    ]
    
    context_parts = []
    current_section = None
    current_content = []
    
    for line in full_context.split("\n"):
        if line.startswith("## "):
            if current_section and any(s in current_section for s in sections_to_include):
                context_parts.append(current_section + "\n" + "\n".join(current_content))
            current_section = line
            current_content = []
        else:
            current_content.append(line)
    
    if current_section and any(s in current_section for s in sections_to_include):
        context_parts.append(current_section + "\n" + "\n".join(current_content))
    
    extracted = "\n\n".join(context_parts)
    
    print(f"Extracted context for unified-review page: {len(extracted)} chars ({len(extracted) / 1024:.1f} KB)")
    print(f"Lines: {len(extracted.splitlines())}")
    print()
    
    # Estimate total prompt size with typical usage
    from src.paperless_firefly.spark_ai.prompts import ChatPrompt
    
    chat_prompt = ChatPrompt()
    
    # Simulate a typical chat request
    question = "How do I transfer bills from Paperless to Firefly?"
    page_context = "Current page: Review & Match & Import\nPage description: Main workflow page"
    conversation_history = [
        {"role": "user", "content": "What is SparkLink?"},
        {"role": "assistant", "content": "SparkLink is a bridge between Paperless-ngx and Firefly III."},
        {"role": "user", "content": "How do I start?"},
        {"role": "assistant", "content": "First, configure your API tokens in Settings."},
    ]
    
    user_message = chat_prompt.format_user_message(
        question=question,
        documentation=extracted,
        page_context=page_context,
        conversation_history=conversation_history,
    )
    
    system_prompt = chat_prompt.system_prompt
    
    print("=" * 80)
    print("ESTIMATED PROMPT SIZES FOR A TYPICAL CHAT REQUEST:")
    print("=" * 80)
    print(f"System prompt: {len(system_prompt)} chars ({len(system_prompt) / 1024:.1f} KB)")
    print(f"User message total: {len(user_message)} chars ({len(user_message) / 1024:.1f} KB)")
    print()
    print(f"  - Documentation context: {len(extracted)} chars")
    print(f"  - Page context: {len(page_context)} chars")
    print(f"  - Conversation history: ~{sum(len(m['content'][:300]) for m in conversation_history)} chars (4 messages)")
    print(f"  - User question: {len(question)} chars")
    print()
    total = len(system_prompt) + len(user_message)
    print(f"TOTAL PROMPT SIZE: {total} chars ({total / 1024:.1f} KB)")
    print()
    
    # Estimate tokens (rough approximation: 1 token ≈ 4 chars)
    estimated_tokens = total / 4
    print(f"Estimated input tokens: ~{estimated_tokens:.0f}")
    print()
    print("NOTE: Response time depends primarily on:")
    print("  1. Your Ollama model speed (qwen2.5:7b is fast, larger models slower)")
    print("  2. CPU/GPU hardware")
    print("  3. Context size (tokens to process)")
    print("  4. Network latency if Ollama is remote")
else:
    print("AI context file not found!")
