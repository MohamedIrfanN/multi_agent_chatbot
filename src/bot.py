import os
import re
import sys
from typing import Dict
import telegram
from time import sleep

# Allow running as a script: `python src/bot.py`
if __package__ is None and __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.tools import desert_tools, has_active_desert_booking
from src.water_tools import water_tools, has_active_water_booking
from src.prompts import (
    DESERT_SYSTEM_PROMPT,
    WATER_SYSTEM_PROMPT,
    GENERAL_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
)

# ----------------------------
# Load env
# ----------------------------
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
TZ = os.getenv("TZ", "Asia/Dubai")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env")

# ----------------------------
# LLM + Agent + Memory
# ----------------------------
llm = ChatOpenAI(model=CHAT_MODEL, temperature=0)

desert_tool_list = desert_tools()
water_tool_list = water_tools()

desert_prompt = ChatPromptTemplate.from_messages([
    ("system", DESERT_SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

water_prompt = ChatPromptTemplate.from_messages([
    ("system", WATER_SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

general_prompt = ChatPromptTemplate.from_messages([
    ("system", GENERAL_SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

router_prompt = ChatPromptTemplate.from_messages([
    ("system", ROUTER_SYSTEM_PROMPT),
    ("human", "last_agent={last_agent}\nuser_message={input}"),
])

desert_agent = create_openai_tools_agent(llm, desert_tool_list, desert_prompt)
water_agent = create_openai_tools_agent(llm, water_tool_list, water_prompt)

# NOTE: This memory is global.
# For production, you should make one memory per user.
memory_store: Dict[str, ConversationBufferWindowMemory] = {}  # key -> memory object
router_state: Dict[str, str] = {}  # user_id -> last_agent
summary_store: Dict[str, str] = {}  # user_id -> accumulated summaries
message_counter: Dict[str, int] = {}  # user_id -> messages since last summary

def get_memory(user_id: str, agent_key: str) -> ConversationBufferWindowMemory:
    key = user_id
    if key not in memory_store:
        memory_store[key] = ConversationBufferWindowMemory(
            k=20,  # keep last 20 turns 
            return_messages=True,
            memory_key="chat_history"
        )
    return memory_store[key]

def make_desert_executor(user_id: str) -> AgentExecutor:
    mem = get_memory(user_id, "desert")
    return AgentExecutor(
        agent=desert_agent,
        tools=desert_tool_list,
        memory=mem,
        verbose=False,
        handle_parsing_errors=True,
        max_iterations=12,
        early_stopping_method="generate"
    )

def make_water_executor(user_id: str) -> AgentExecutor:
    mem = get_memory(user_id, "water")
    return AgentExecutor(
        agent=water_agent,
        tools=water_tool_list,
        memory=mem,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=12,
        early_stopping_method="generate"
    )

def run_general_agent(user_id: str, user_text: str) -> str:
    mem = get_memory(user_id, "general")
    history = mem.load_memory_variables({}).get("chat_history", [])
    messages = general_prompt.format_messages(input=user_text, chat_history=history)
    response = llm.invoke(messages)
    mem.save_context({"input": user_text}, {"output": response.content})
    return (response.content or "").strip()

_DESERT_KEYWORDS = re.compile(r"\b(desert|buggy|quad|safari|dune|camp)\b", re.IGNORECASE)
_WATER_KEYWORDS = re.compile(
    r"\b(jetski|jet\s*ski|flyboard|jet\s*car|water|water\s*activity|water\s*sport|watersport|burj|atlantis|jbr|royal\s+atlantis)\b",
    re.IGNORECASE,
)
_FOLLOWUP_KEYWORDS = re.compile(r"^(yes|yeah|yep|ok|okay|sure|confirm|book|book it|proceed|go ahead|no|not now)\b", re.IGNORECASE)
_TIME_OR_DATE_RE = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm)|tomorrow|today|tonight|next\s+\w+|day after tomorrow|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b",
    re.IGNORECASE,
)
_PRICE_INQUIRY_RE = re.compile(r"\b(price|cost|how much|rate|quote|discount)\b", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b\d+\s*(min|minute|minutes)\b", re.IGNORECASE)
_BOTH_PACKAGES_RE = re.compile(r"\bboth\b.*\bpackage", re.IGNORECASE)
_BOTH_SHOW_RE = re.compile(r"\b(show|display|list)\b.*\bboth\b", re.IGNORECASE)
_ALL_PACKAGES_RE = re.compile(r"\ball\b.*\bpackage", re.IGNORECASE)

_BASE_TOUR_DURATIONS = [
    ("burj khalifa", 20),
    ("burj al arab", 30),
    ("burj alarab", 30),
    ("royal atlantis", 60),
    ("atlantis", 90),
    ("jbr", 120),
]

def _is_price_inquiry(text: str) -> bool:
    return _PRICE_INQUIRY_RE.search(text) is not None

def _has_duration(text: str) -> bool:
    return _DURATION_RE.search(text) is not None

def _infer_base_duration(text: str) -> int | None:
    lower = text.lower()
    for name, base in _BASE_TOUR_DURATIONS:
        if name in lower:
            return base
    return None

def _wants_both_packages(text: str) -> bool:
    lower = text.lower()
    if _BOTH_PACKAGES_RE.search(lower):
        return True
    if _BOTH_SHOW_RE.search(lower):
        return True
    if "water" in lower and "desert" in lower and "package" in lower:
        return True
    if _ALL_PACKAGES_RE.search(lower) and "water" not in lower and "desert" not in lower:
        return True
    return False

def _is_short_followup(text: str) -> bool:
    words = re.findall(r"[a-zA-Z0-9']+", text.strip())
    return 0 < len(words) <= 4

def _get_last_agent(user_id: str) -> str:
    return router_state.get(user_id, "general")

def _set_last_agent(user_id: str, agent_key: str) -> None:
    router_state[user_id] = agent_key

def _increment_message_count(user_id: str) -> int:
    """Increment and return message count for user."""
    current = message_counter.get(user_id, 0)
    message_counter[user_id] = current + 1
    return message_counter[user_id]

def _get_accumulated_summary(user_id: str) -> str:
    """Get accumulated summaries for user."""
    return summary_store.get(user_id, "")

def _reset_summary(user_id: str) -> None:
    """Reset summary counter and store after booking confirmed."""
    message_counter[user_id] = 0
    if user_id in summary_store:
        del summary_store[user_id]

def _generate_summary(user_id: str) -> None:
    """Generate summary of last 20 messages and accumulate."""
    mem = get_memory(user_id, "summary")
    history = mem.load_memory_variables({}).get("chat_history", [])
    
    if not history:
        return
    
    # Build conversation text from last ~20 messages
    text_parts = []
    for msg in history[-20:]:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if content:
            text_parts.append(f"{role}: {content}")
    
    if not text_parts:
        return
    
    conversation_text = "\n".join(text_parts)
    
    # Quick summary prompt to extract booking context
    summary_prompt = f"""Summarize the key booking details from this conversation. 
Preserve: activity type, vehicle/package name, duration, date/time, price, discount, any booking stages reached.
Keep it concise (2-3 sentences):

{conversation_text}

SUMMARY:"""
    
    try:
        response = llm.invoke([("human", summary_prompt)])
        summary_text = (response.content or "").strip()
        
        # Accumulate with previous summary
        if user_id in summary_store:
            summary_store[user_id] += f"\n{summary_text}"
        else:
            summary_store[user_id] = summary_text
    except Exception:
        pass  # If summary fails, don't break the flow

def _format_agent_input_with_summary(user_id: str, user_text: str) -> str:
    """Format agent input with booking context if available."""
    acc_summary = _get_accumulated_summary(user_id)
    if acc_summary:
        return f"[BOOKING_CONTEXT]\n{acc_summary}\n[/BOOKING_CONTEXT]\n\n[user_id={user_id}] {user_text}"
    return f"[user_id={user_id}] {user_text}"

def route_agent(user_id: str, user_text: str) -> str:
    water_match = _WATER_KEYWORDS.search(user_text)
    desert_match = _DESERT_KEYWORDS.search(user_text)
    if water_match and desert_match:
        return "clarify"
    if water_match:
        return "water"
    if desert_match:
        return "desert"

    if has_active_water_booking(user_id):
        return "water"
    if has_active_desert_booking(user_id):
        return "desert"

    last_agent = _get_last_agent(user_id)
    if last_agent in {"desert", "water"}:
        if _FOLLOWUP_KEYWORDS.search(user_text.strip()):
            return last_agent
        if _TIME_OR_DATE_RE.search(user_text) or _is_short_followup(user_text):
            return last_agent

    messages = router_prompt.format_messages(input=user_text, last_agent=last_agent)
    response = llm.invoke(messages)
    route_text = (response.content or "").strip().lower()
    match = re.search(r"\b(desert|water|general|clarify)\b", route_text)
    return match.group(1) if match else "general"

def _enforce_single_question(reply: str) -> str:
    lower = reply.lower()
    q_idx = reply.find("?")
    if q_idx != -1:
        tail = lower[q_idx + 1:]
        if "if yes" in tail or "if so" in tail or "if you want" in tail or "if you'd like" in tail or "if you would like" in tail:
            return reply[:q_idx + 1].strip()
    if reply.count("?") <= 1:
        return reply
    if "http://" in reply or "https://" in reply:
        return reply
    first_idx = reply.find("?")
    return reply[:first_idx + 1].strip()

def _strip_payment_questions(reply: str) -> str:
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    filtered = []
    for line in lines:
        lower = line.lower()
        if "payment method" in lower or "payment options" in lower:
            if line.endswith("?") or lower.startswith("please let me know"):
                continue
        if "cash" in lower and "card" in lower and "crypt" in lower and line.endswith("?"):
            continue
        filtered.append(line)
    cleaned = " ".join(filtered).strip()
    if not cleaned:
        return cleaned
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    kept = []
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        lower = s.lower()
        if "payment method" in lower or "preferred payment" in lower or "which payment" in lower:
            continue
        if "cash" in lower and "card" in lower and "crypt" in lower:
            continue
        if lower.startswith("if yes") or lower.startswith("if so"):
            if "payment" in lower or "book" in lower or "proceed" in lower:
                continue
        if s.endswith("?") and (lower.startswith("would you like") or lower.startswith("do you want")):
            if "book" in lower or "booking" in lower or "proceed" in lower:
                continue
        kept.append(s)
    return " ".join(kept).strip()

# ----------------------------
# Telegram handlers
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I am Jetset Dubai's assistant. I can help with desert and water activities, packages, prices, and bookings. What would you like to do?"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "You can ask about desert activities (Safari, Quad, Buggy) or water activities (Jet Ski, Flyboard, Jet Car), plus pricing, pickup, location, rules, or say you want to book and I will guide you."
    )

def extract_user_text(update: Update) -> str:
    return (update.message.text or "").strip()

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_text = extract_user_text(update)

    if not user_text:
        await update.message.reply_text("I didn’t catch that. Please type your message.")
        return
    # Increment message counter and check if we should generate summary
    msg_count = _increment_message_count(user_id)
    if msg_count % 20 == 0:
        _generate_summary(user_id)
        message_counter[user_id] = 0  # Reset counter after summary
    if _wants_both_packages(user_text):
        # Extract date/time hint if user mentioned it
        time_match = _TIME_OR_DATE_RE.search(user_text)
        date_hint = ""
        if time_match:
            date_hint = f" (User mentioned: {time_match.group(1)})"
        
        desert_executor = make_desert_executor(user_id)
        water_executor = make_water_executor(user_id)
        desert_result = desert_executor.invoke({"input": f"[user_id={user_id}] List buggy, quad, and safari packages with prices.{date_hint}"})
        water_result = water_executor.invoke({"input": f"[user_id={user_id}] Show all water packages.{date_hint}"})
        desert_reply = _enforce_single_question((desert_result.get("output") or "").strip())
        water_reply = _enforce_single_question((water_result.get("output") or "").strip())
        reply = f"Desert packages:\n{desert_reply}\n\nWater packages:\n{water_reply}"
        await update.message.reply_text(reply)
        return

    if _ALL_PACKAGES_RE.search(user_text.lower()):
        lower = user_text.lower()
        if "water" not in lower and "desert" not in lower:
            reply = "Do you want desert packages, water packages, or both?"
            await update.message.reply_text(reply)
            return

    route = route_agent(user_id, user_text)

    try:
        if route == "desert":
            # Pass user_id inline so the agent can use it when calling booking tools
            agent_input = _format_agent_input_with_summary(user_id, user_text)
            executor = make_desert_executor(user_id)
            result = executor.invoke({"input": agent_input})
            reply = _enforce_single_question((result.get("output") or "").strip())
            _set_last_agent(user_id, "desert")
        elif route == "water":
            hinted_text = user_text
            if _is_price_inquiry(user_text):
                hints = ["price inquiry only; provide the price without booking questions"]
                if not _has_duration(user_text):
                    base_duration = _infer_base_duration(user_text)
                    if base_duration:
                        hints.append(f"use base duration {base_duration} minutes")
                if hints:
                    hinted_text = f"{user_text} ({'; '.join(hints)})"
            agent_input = _format_agent_input_with_summary(user_id, hinted_text)
            executor = make_water_executor(user_id)
            result = executor.invoke({"input": agent_input})
            reply = _enforce_single_question((result.get("output") or "").strip())
            if _is_price_inquiry(user_text):
                reply = _strip_payment_questions(reply)
            _set_last_agent(user_id, "water")
        elif route == "clarify":
            reply = run_general_agent(user_id, user_text)
        else:
            reply = run_general_agent(user_id, user_text)

        if not reply:
            reply = "Sorry — I couldn’t generate a response. Try again."
    except Exception as e:
        reply = f"Sorry, something went wrong: {e}"

    await update.message.reply_text(reply)

# ----------------------------
# Main
# ----------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("✅ Bot is running (polling)...")
    app.run_polling()

if __name__ == "__main__":
    main()
