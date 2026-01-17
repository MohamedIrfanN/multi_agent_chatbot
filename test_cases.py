import argparse
import json
import re
import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import src.bot as bot
from src.tools import BOOKINGS
from src.water_tools import WATER_BOOKINGS

EVAL_MODEL = os.getenv("EVAL_MODEL", os.getenv("CHAT_MODEL", "gpt-4.1-mini"))
EVAL_LLM = ChatOpenAI(model=EVAL_MODEL, temperature=0)

SCORE_RUBRIC = {
    "accuracy": [
        ("Excellent", "90-100%"),
        ("Good", "80-89%"),
        ("Satisfactory", "70-79%"),
        ("Needs Improvement", "60-69%"),
        ("Poor", "Below 60%"),
    ],
    "speed_latency": [
        ("Best (Excellent)", "< 4 seconds"),
        ("High (Good)", "4 - 5 seconds"),
        ("Acceptable (Satisfactory)", "5 - 6 seconds"),
        ("Low (Needs Improvement)", "6 - 7 seconds"),
        ("Poor (Unsatisfactory)", "Above 7 seconds"),
    ],
    "robustness": [
        ("Best (Excellent)", "90-100%"),
        ("High (Good)", "80-89%"),
        ("Acceptable (Satisfactory)", "70-79%"),
        ("Low (Needs Improvement)", "60-69%"),
        ("Poor (Unsatisfactory)", "Below 60%"),
    ],
    "user_experience": [
        ("Best (Excellent)", "90-100%"),
        ("High (Good)", "80-89%"),
        ("Acceptable (Satisfactory)", "70-79%"),
        ("Low (Needs Improvement)", "60-69%"),
        ("Poor (Unsatisfactory)", "Below 60%"),
    ],
    "memory_context_retention": [
        ("Best (Excellent)", "90-100%"),
        ("High (Good)", "80-89%"),
        ("Acceptable (Satisfactory)", "70-79%"),
        ("Low (Needs Improvement)", "60-69%"),
        ("Poor (Unsatisfactory)", "Below 60%"),
    ],
    "language_understanding": [
        ("Best (Excellent)", "90-100%"),
        ("High (Good)", "80-89%"),
        ("Acceptable (Satisfactory)", "70-79%"),
        ("Low (Needs Improvement)", "60-69%"),
        ("Poor (Unsatisfactory)", "Below 60%"),
    ],
    "cost_efficiency": [
        ("Best (Excellent)", "< 0.01 USD"),
        ("High (Good)", "0.01 - 0.02 USD"),
        ("Acceptable (Satisfactory)", "0.02 - 0.05 USD"),
        ("Low (Needs Improvement)", "0.05 - 0.10 USD"),
        ("Poor (Unsatisfactory)", "Above 0.10 USD"),
    ],
}

TEST_CASES: List[Dict[str, object]] = [
    {
        "id": "case1",
        "title": "General Inquiries",
        "scenario": "Send greeting and farewells",
        "expectation": "The supervisor agent should reply.",
        "turns": ["Hi", "Bye"],
    },
    {
        "id": "case2",
        "title": "General Inquiries",
        "scenario": "Ask general questions about our activities and our business",
        "expectation": "Supervisor should mention water and desert services.",
        "turns": ["What is your business area?"],
    },
    {
        "id": "case3",
        "title": "Customer Hesitation",
        "scenario": "Unsure about which activity to choose and ask for suggestions",
        "expectation": "Supervisor suggests activities.",
        "turns": ["I'm not sure which activity to choose. Any suggestions?"],
    },
    {
        "id": "case4",
        "title": "Cross-Tool Inquiries",
        "scenario": "Ask for location and opening hours in the same request",
        "expectation": "Desired agent replies with location and FAQ info.",
        "turns": ["For water activities, where are you located and what are your opening hours?"],
    },
    {
        "id": "case5",
        "title": "Complex Seasonal Pricing",
        "scenario": "Book a jet ski in a different season and ask for discount",
        "expectation": "Water agent responds with high season pricing rules.",
        "turns": ["I want to book a jet ski for 25 Nov, is there any discount?"],
    },
    {
        "id": "case6",
        "title": "Date Handling",
        "scenario": "Book for tomorrow and see booking date in summary",
        "expectation": "Booking date should appear in summary.",
        "turns": ["I want to book Burj Khalifa 20 minutes", "2", "tomorrow 4pm", "cash", "Irfan"],
    },
    {
        "id": "case7",
        "title": "Location Handling",
        "scenario": "Ask about location during booking process",
        "expectation": "Location should be added automatically in summary.",
        "turns": [
            "I want to book Burj Khalifa 20 minutes",
            "2",
            "Where are you located?",
            "tomorrow 4pm",
            "card",
            "Irfan",
        ],
    },
    {
        "id": "case8",
        "title": "Cross-Category Inquiries",
        "scenario": "Book water and desert activities in the same conversation",
        "expectation": "Assistant maintains context for both categories.",
        "turns": ["I want to book a water activity and a desert activity", "Show options for both"],
    },
    {
        "id": "case9",
        "title": "FAQ Handling",
        "scenario": "Ask FAQ questions (minimum age, payment, refund)",
        "expectation": "Desired agent uses FAQ tool.",
        "turns": ["For water activities, what is the minimum age and refund policy?"],
    },
    {
        "id": "case10",
        "title": "Package Customization",
        "scenario": "Combine activities (jet ski + buggy) and ask for pricing",
        "expectation": "Assistant combines relevant packages and pricing.",
        "turns": ["Can I do a Jet Ski ride followed by a Buggy tour? What are the prices for both, and is there a discount?"],
    },
    {
        "id": "case11",
        "title": "Multi-Step Booking Process",
        "scenario": "Full booking with multiple clarifications",
        "expectation": "Assistant maintains context and confirms final details.",
        "turns": [
            "I want to book a quad",
            "Polaris Sportsman 570cc",
            "2",
            "30 minutes",
            "tomorrow 4pm",
            "yes pickup",
            "card",
            "Irfan",
            "confirm",
        ],
    },
    {
        "id": "case12",
        "title": "Discount Eligibility Check",
        "scenario": "Ask about discount eligibility based on season",
        "expectation": "Assistant checks and verifies discount eligibility.",
        "turns": ["Can I get a discount if I book jet ski for 6 people on 25 Nov?"],
    },
    {
        "id": "case13",
        "title": "Clarification of Similar Terms",
        "scenario": "Ask difference between Jet Car and Jet Ski",
        "expectation": "Assistant differentiates the activities.",
        "turns": ["What's the difference between a Jet Car and a Jet Ski? Which one is faster?"],
    },
    {
        "id": "case14",
        "title": "Booking in One Step",
        "scenario": "Send all booking details in one request",
        "expectation": "Assistant guides smoothly and confirms details.",
        "turns": [
            "Hi, my name is Wael Boussabat, my number is 12345678. I want to book Burj Al Arab tour for tomorrow 10am.",
        ],
    },
    {
        "id": "case15",
        "title": "Booking with Duration",
        "scenario": "Request booking with duration but no activity name",
        "expectation": "Assistant asks which activity/package.",
        "turns": ["I want to book 30 minutes tour for tomorrow 10am."],
    },
    {
        "id": "case16",
        "title": "Booking with Both",
        "scenario": "Ask to book both desert and water at the beginning",
        "expectation": "Assistant asks which activity to start with.",
        "turns": ["I would like to pick a desert activity and a water activity."],
    },
    {
        "id": "case17",
        "title": "Booking Without Time",
        "scenario": "Book without providing a time",
        "expectation": "Assistant insists on a time before confirming.",
        "turns": ["I want to book Burj Al Arab 30 minutes for tomorrow, 2 jet skis."],
    },
    {
        "id": "case18",
        "title": "Booking with Desert Keyword",
        "scenario": "Use desert tour/machine type keywords",
        "expectation": "Assistant routes to the correct desert package.",
        "turns": ["I would like to book an Aon Cobra."],
    },
    {
        "id": "case19",
        "title": "Booking Duration Past Opening Hours",
        "scenario": "Book with time that would pass opening hours",
        "expectation": "Assistant rejects and asks for a new time.",
        "turns": ["I want to book JBR 120 minutes at 6 PM."],
    },
    {
        "id": "case20",
        "title": "Booking Both Without Duration",
        "scenario": "Book water and desert without specifying duration for either",
        "expectation": "Assistant does not assume duration and asks for details.",
        "turns": ["I want to book a water activity and a desert activity, no duration specified."],
    },
    {
        "id": "case21",
        "title": "Water Packages Only",
        "scenario": "Request all water packages explicitly",
        "expectation": "Assistant lists only water packages without asking which activity.",
        "turns": ["Show only water packages."],
    },
    {
        "id": "case22",
        "title": "All Packages Both Categories",
        "scenario": "Request all packages without specifying category",
        "expectation": "Assistant lists both desert and water packages together.",
        "turns": ["Show all packages."],
    },
]

EVAL_SYSTEM_PROMPT = """You are a strict evaluator for a Jetset Dubai assistant.
Score the assistant against the scenario and expectation.
Return ONLY JSON with this schema:
{
  "scores": {
    "accuracy": 0-100,
    "robustness": 0-100,
    "user_experience": 0-100,
    "memory_context_retention": 0-100,
    "language_understanding": 0-100,
    "speed_latency": null,
    "cost_efficiency": null
  },
  "overall_score": 0-100,
  "notes": "one short sentence about the main issue or success"
}
Do not include any extra keys or text. Set speed_latency and cost_efficiency to null if not measurable."""


def reset_user_state(user_id: str) -> None:
    for key in list(bot.memory_store.keys()):
        if key.startswith(f"{user_id}:"):
            bot.memory_store.pop(key, None)
    bot.router_state.pop(user_id, None)
    BOOKINGS.pop(user_id, None)
    WATER_BOOKINGS.pop(user_id, None)


def send(user_id: str, text: str) -> str:
    if bot._wants_both_packages(text):
        desert_executor = bot.make_desert_executor(user_id)
        water_executor = bot.make_water_executor(user_id)
        desert_result = desert_executor.invoke({"input": f"[user_id={user_id}] List buggy, quad, and safari packages with prices."})
        water_result = water_executor.invoke({"input": f"[user_id={user_id}] Show all water packages."})
        desert_reply = bot._enforce_single_question((desert_result.get("output") or "").strip())
        water_reply = bot._enforce_single_question((water_result.get("output") or "").strip())
        return f"Desert packages:\n{desert_reply}\n\nWater packages:\n{water_reply}"

    route = bot.route_agent(user_id, text)
    if route == "desert":
        agent_input = f"[user_id={user_id}] {text}"
        executor = bot.make_desert_executor(user_id)
        result = executor.invoke({"input": agent_input})
        reply = bot._enforce_single_question((result.get("output") or "").strip())
        bot._set_last_agent(user_id, "desert")
    elif route == "water":
        hinted_text = text
        if bot._is_price_inquiry(text):
            hints = ["price inquiry only; provide the price without booking questions"]
            if not bot._has_duration(text):
                base = bot._infer_base_duration(text)
                if base:
                    hints.append(f"use base duration {base} minutes")
            hinted_text = f"{text} ({'; '.join(hints)})"
        agent_input = f"[user_id={user_id}] {hinted_text}"
        executor = bot.make_water_executor(user_id)
        result = executor.invoke({"input": agent_input})
        reply = bot._enforce_single_question((result.get("output") or "").strip())
        if bot._is_price_inquiry(text):
            reply = bot._strip_payment_questions(reply)
        bot._set_last_agent(user_id, "water")
    elif route == "clarify":
        reply = "Do you mean desert activities (buggy/quad/safari) or water activities (jet ski/flyboard/jet car)?"
    else:
        reply = bot.run_general_agent(user_id, text)
    return reply


def list_cases() -> None:
    for case in TEST_CASES:
        print(f"{case['id']}: {case['title']} - {case['scenario']}")

def _extract_json(text: str) -> Dict[str, object]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

def llm_evaluate_case(case: Dict[str, object], replies: List[str]) -> Dict[str, object]:
    transcript_lines = []
    for idx, (user_text, bot_text) in enumerate(zip(case["turns"], replies), 1):
        transcript_lines.append(f"User {idx}: {user_text}")
        transcript_lines.append(f"Bot {idx}: {bot_text}")
    transcript = "\n".join(transcript_lines)

    user_prompt = (
        f"Scenario: {case['scenario']}\n"
        f"Expectation: {case['expectation']}\n"
        f"Transcript:\n{transcript}"
    )
    messages = [
        SystemMessage(content=EVAL_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    response = EVAL_LLM.invoke(messages)
    data = _extract_json(response.content or "")
    return data

def _average_score(scores: Dict[str, object]) -> int:
    numeric = [v for v in scores.values() if isinstance(v, (int, float))]
    if not numeric:
        return 0
    return int(round(sum(numeric) / len(numeric)))

def run_cases(case_ids: List[str], llm_eval: bool) -> None:
    overall_scores: List[int] = []
    for case in TEST_CASES:
        if case_ids and case["id"] not in case_ids:
            continue
        user_id = case["id"]
        reset_user_state(user_id)
        print("\n" + "=" * 80)
        print(f"Case: {case['id']} - {case['title']}")
        print(f"Scenario: {case['scenario']}")
        print(f"Expectation: {case['expectation']}")
        replies: List[str] = []
        for i, text in enumerate(case["turns"], 1):
            reply = send(user_id, text)
            replies.append(reply)
            print(f"User {i}: {text}")
            print(f"Bot  {i}: {reply}")
        if llm_eval:
            eval_result = llm_evaluate_case(case, replies)
            scores = eval_result.get("scores", {}) if isinstance(eval_result, dict) else {}
            overall = eval_result.get("overall_score")
            if not isinstance(overall, (int, float)):
                overall = _average_score(scores if isinstance(scores, dict) else {})
            overall_scores.append(int(overall))
            print(f"LLM scores: {scores}")
            print(f"LLM overall: {int(overall)}")
            notes = eval_result.get("notes")
            if notes:
                print(f"LLM notes: {notes}")
    if llm_eval and overall_scores:
        avg = int(round(sum(overall_scores) / len(overall_scores)))
        print("\n" + "=" * 80)
        print(f"Average LLM overall: {avg}")


def show_rubric() -> None:
    print("\nScoring Rubric:")
    for key, rows in SCORE_RUBRIC.items():
        print(f"- {key}:")
        for label, range_text in rows:
            print(f"  - {label}: {range_text}")


def save_test_output(case_ids: List[str], llm_eval: bool, output_filename: str = "test_output.md") -> None:
    """Save complete test output to a markdown file in docs directory."""
    import time
    import sys
    output_lines: List[str] = []
    overall_scores: List[int] = []
    
    output_lines.append("# Test Case Results\n")
    output_lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    output_lines.append("---\n")
    
    # Filter cases to run
    cases_to_run = [case for case in TEST_CASES if not case_ids or case["id"] in case_ids]
    total_cases = len(cases_to_run)
    
    for idx, case in enumerate(cases_to_run, 1):
        user_id = case["id"]
        reset_user_state(user_id)
        
        # Progress indicator
        print(f"\n[{idx}/{total_cases}] Processing: {case['id']} - {case['title']}")
        sys.stdout.flush()
        
        output_lines.append(f"\n## {case['id']}: {case['title']}\n")
        output_lines.append(f"**Scenario:** {case['scenario']}\n")
        output_lines.append(f"**Expectation:** {case['expectation']}\n")
        output_lines.append("\n### Transcript\n")
        
        replies: List[str] = []
        for i, text in enumerate(case["turns"], 1):
            reply = send(user_id, text)
            replies.append(reply)
            output_lines.append(f"\n**User {i}:** {text}\n")
            output_lines.append(f"**Bot {i}:** {reply}\n")
        
        if llm_eval:
            print(f"  Evaluating with LLM...", end="", flush=True)
            eval_result = llm_evaluate_case(case, replies)
            scores = eval_result.get("scores", {}) if isinstance(eval_result, dict) else {}
            overall = eval_result.get("overall_score")
            if not isinstance(overall, (int, float)):
                overall = _average_score(scores if isinstance(scores, dict) else {})
            overall_scores.append(int(overall))
            print(f" Done! Score: {int(overall)}/100")
            
            output_lines.append(f"\n### Evaluation Results\n")
            output_lines.append(f"**Overall Score:** {int(overall)}/100\n")
            if isinstance(scores, dict):
                output_lines.append(f"\n**Scores:**\n")
                for key, value in scores.items():
                    output_lines.append(f"- {key}: {value}\n")
            notes = eval_result.get("notes")
            if notes:
                output_lines.append(f"\n**Notes:** {notes}\n")
    
    if llm_eval and overall_scores:
        avg = int(round(sum(overall_scores) / len(overall_scores)))
        output_lines.append(f"\n---\n")
        output_lines.append(f"## Summary\n")
        output_lines.append(f"**Average LLM Overall Score:** {avg}/100\n")
    
    # Create docs directory if it doesn't exist
    docs_dir = os.path.join(os.getcwd(), "docs")
    if not os.path.exists(docs_dir):
        os.makedirs(docs_dir)
    
    # Save to file
    output_path = os.path.join(docs_dir, output_filename)
    with open(output_path, "w") as f:
        f.writelines(output_lines)
    
    print(f"\n✓ Test output saved to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Jetset chatbot test cases.")
    parser.add_argument("--list", action="store_true", help="List available test cases.")
    parser.add_argument("--only", nargs="*", default=[], help="Run only specific case IDs.")
    parser.add_argument("--llm-eval", action="store_true", help="Evaluate test cases with the LLM.")
    parser.add_argument("--show-rubric", action="store_true", help="Print the scoring rubric.")
    parser.add_argument("--save", type=str, nargs="?", const="test_output.md", help="Save test output to docs file.")
    args = parser.parse_args()

    if args.list:
        list_cases()
        return

    if args.save:
        save_test_output(args.only, args.llm_eval, args.save)
    else:
        run_cases(args.only, args.llm_eval)
    
    if args.show_rubric:
        show_rubric()


if __name__ == "__main__":
    main()
