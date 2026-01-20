import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.tools import tool

# ----------------------------
# Env
# ----------------------------
WATER_CHROMA_DIR = os.getenv("WATER_CHROMA_DIR", "data/water_chroma_db")
WATER_CHROMA_COLLECTION = os.getenv("WATER_CHROMA_COLLECTION", "jetset_water_kb")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
TZ = os.getenv("TZ", "Asia/Dubai")

# ----------------------------
# Vector DB / Retriever
# ----------------------------
_embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
_water_db = Chroma(
    collection_name=WATER_CHROMA_COLLECTION,
    persist_directory=WATER_CHROMA_DIR,
    embedding_function=_embeddings,
)

def _water_search(query: str, k: int = 6) -> Dict[str, Any]:
    docs = _water_db.similarity_search(query, k=k)
    matches = [{"text": d.page_content, "meta": d.metadata} for d in docs]
    return {"query": query, "matches": matches}

# ----------------------------
# Tools (Water)
# ----------------------------
@tool
def water_current_datetime_tool(tz: str = "Asia/Dubai") -> str:
    """Return the current datetime (ISO string) in the given timezone. Used to resolve 'tomorrow', etc."""
    now = datetime.now(ZoneInfo(tz))
    return json.dumps({"tz": tz, "now_iso": now.isoformat()})

# ----------------------------
# Pricing
# ----------------------------
CARD_VAT_MULTIPLIER = 1.05

# ----------------------------
# Booking Store (separate from desert)
# ----------------------------
WATER_BOOKINGS: Dict[str, Dict[str, Any]] = {}
_CURRENT_WATER_USER_ID: Optional[str] = None

def set_current_water_user_id(user_id: Optional[str]) -> None:
    """Set the current user id so tools can infer booking_date when missing."""
    global _CURRENT_WATER_USER_ID
    _CURRENT_WATER_USER_ID = user_id

def _infer_booking_date_from_context() -> Optional[str]:
    """Best-effort booking_date from current user's draft."""
    if not _CURRENT_WATER_USER_ID:
        return None
    draft = WATER_BOOKINGS.get(_CURRENT_WATER_USER_ID, {})
    dt_value = draft.get("booking_date") or draft.get("date_time_iso")
    if not dt_value:
        return None
    return str(dt_value).split("T")[0]
_JETSKI_BASE_DURATIONS = [
    ("burj khalifa", 20),
    ("burj al arab", 30),
    ("burj alarab", 30),
    ("royal atlantis", 60),
    ("atlantis", 90),
    ("jbr", 120),
]

def _get_or_create_water_booking(user_id: str) -> Dict[str, Any]:
    if user_id not in WATER_BOOKINGS:
        WATER_BOOKINGS[user_id] = {
            "status": "collecting",          # collecting | ready_to_confirm | confirmed
            "customer_name": None,
            "activity": None,                # jetski | flyboard | jetcar
            "package": None,                 # tour/package name (optional)
            "quantity": None,                # number of vehicles
            "duration_min": None,            # 20/30/60/90/120 etc.
            "date_time_iso": None,           # ISO datetime in Dubai TZ (stored internally)
            "pickup_required": False,        # Water bookings do not offer pickup
            "payment_method": None,          # cash/card/crypto
            "price_aed": None,
            "price_aed_base": None,          # Base price before VAT (used for recalc when payment method changes)
            "items": [],                     # list of activity line items
            "notes": []
        }
    else:
        WATER_BOOKINGS[user_id].setdefault("items", [])
    return WATER_BOOKINGS[user_id]

def _normalize_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"yes", "y", "true", "1"}:
        return True
    if s in {"no", "n", "false", "0"}:
        return False
    return None

def _jetski_base_duration(package: Optional[str]) -> Optional[int]:
    if not package:
        return None
    cleaned = str(package).lower().replace("-", " ")
    for name, base in _JETSKI_BASE_DURATIONS:
        if name in cleaned:
            return base
    return None

def _get_season_for_date(date_str: str) -> str:
    """Determine season (High/Low/Summer End) for a given date.
    
    Args:
        date_str: ISO date string "YYYY-MM-DD"
    
    Returns:
        "high_season" | "low_season" | "summer_end"
    """
    try:
        date = datetime.fromisoformat(date_str)
        month = date.month
        day = date.day
        
        # High Season: Nov 15 - Mar 15
        if (month == 11 and day >= 15) or month == 12 or (month == 1) or (month == 2) or (month == 3 and day <= 15):
            return "high_season"
        
        # Low Season: Mar 16 - Aug 31
        elif (month == 3 and day >= 16) or (4 <= month <= 8):
            return "low_season"
        
        # Summer End: Sep 1 - Nov 14
        else:  # Sep, Oct, or Nov (before 15)
            return "summer_end"
    except Exception:
        return "high_season"  # Default fallback

def _parse_start_dt(dt_value: str) -> Optional[datetime]:
    """Parse ISO datetime string and ensure it's in Dubai timezone."""
    try:
        parsed = datetime.fromisoformat(dt_value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo(TZ))
    return parsed.astimezone(ZoneInfo(TZ))

def _validate_booking_times(draft: Dict[str, Any]) -> Optional[str]:
    """Validate booking times against opening hours (9am-7pm). Returns error message or None."""
    items = draft.get("items") or []
    bookings_to_check = items if items else [draft]
    
    for booking in bookings_to_check:
        dt_value = booking.get("date_time_iso") or draft.get("date_time_iso")
        duration = booking.get("duration_min") or draft.get("duration_min")
        
        if not dt_value or not duration:
            continue
            
        start_dt = _parse_start_dt(str(dt_value))
        if start_dt is None:
            return "Invalid booking time format."
            
        opening_start = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        opening_end = start_dt.replace(hour=19, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(minutes=int(duration))
        
        if start_dt < opening_start or end_dt > opening_end:
            return "Booking time must start after 9am and finish by 7pm. Please choose a time that fits the full duration."
    
    return None

def _validate_jetski_duration(draft: Dict[str, Any]) -> Optional[str]:
    """Validate duration based on activity type. Returns error message or None."""
    items = draft.get("items") or []
    bookings_to_check = items if items else [draft]
    
    for booking in bookings_to_check:
        activity = (booking.get("activity") or "").strip().lower()
        duration = booking.get("duration_min")
        
        if not duration:
            continue
        
        duration = int(duration)
        
        # JET SKI: Must be multiple of base duration
        if activity in {"jetski", "jet ski"}:
            base = _jetski_base_duration(booking.get("package"))
            if base and duration % base != 0:
                return f"Invalid duration for {booking.get('package')}. Must be a multiple of {base} minutes."
        
        # FLYBOARD: Validate with greedy algorithm (bases: 30, 20)
        elif activity == "flyboard":
            if not _can_divide_duration([30, 20], duration):
                return f"Duration {duration} minutes is not available for flyboard. Try a different duration."
        
        # JET CAR: Validate with greedy algorithm (bases: 60, 30, 20)
        elif activity in {"jetcar", "jet car"}:
            if not _can_divide_duration([60, 30, 20], duration):
                return f"Duration {duration} minutes is not available for jet car. Try a different duration."
    
    return None

def _can_divide_duration(bases: list, duration: int) -> bool:
    """Check if duration can be divided into bases using any valid combination.
    
    Uses BFS to find if duration can be made by summing any combination of bases.
    For example:
    - _can_divide_duration([30, 20], 140) → True (4×30 + 1×20)
    - _can_divide_duration([60, 30, 20], 100) → True (1×60 + 2×20)
    - _can_divide_duration([30, 20], 25) → False (no valid combination)
    
    Args:
        bases: List of base durations (e.g., [30, 20] for flyboard, [60, 30, 20] for jet car)
        duration: Target duration in minutes
    
    Returns:
        True if duration can be made from bases, False otherwise
    """
    from collections import deque
    
    visited = {duration}
    queue = deque([duration])
    
    while queue:
        remaining = queue.popleft()
        
        if remaining == 0:
            return True
        
        if remaining < 0:
            continue
        
        # Try subtracting each base
        for base in bases:
            new_remaining = remaining - base
            if new_remaining >= 0 and new_remaining not in visited:
                visited.add(new_remaining)
                queue.append(new_remaining)
    
    return False

def _validate_time_immediately(draft: Dict[str, Any]) -> Optional[str]:
    """
    Check if we have enough info to validate time (date_time_iso + duration_min).
    If both exist, validate immediately. Returns error message if invalid, None if valid or not enough info.
    This validates BEFORE other fields are collected.
    """
    dt_value = draft.get("date_time_iso")
    duration = draft.get("duration_min")
    
    # Only validate if BOTH are present
    if not dt_value or not duration:
        return None
    
    # Parse and validate
    start_dt = _parse_start_dt(str(dt_value))
    if start_dt is None:
        return "Invalid booking time format."
    
    opening_start = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    opening_end = start_dt.replace(hour=19, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(minutes=int(duration))
    
    if start_dt < opening_start or end_dt > opening_end:
        return "Booking time must start after 9am and finish by 7pm. Please choose a time that fits the full duration."
    
    return None

def _normalize_value(value: Any, key: str) -> Any:
    """Normalize a single value based on key type."""
    if value is None:
        return None
    
    if key == "pickup_required":
        return _normalize_bool(value)
    
    if key in {"activity", "payment_method"}:
        return str(value).strip().lower()
    
    if key in {"quantity", "duration_min"}:
        try:
            return int(value)
        except Exception:
            return value
    
    if key == "price_aed":
        try:
            return float(value)
        except Exception:
            return value
    
    return value

@tool
def water_booking_get_or_create(user_id: str) -> str:
    """Get or create a water booking draft object for a given Telegram user_id."""
    draft = _get_or_create_water_booking(user_id)
    return json.dumps(draft)

@tool
def water_booking_update(
    user_id: str,
    patch: Optional[dict] = None,
    customer_name: Optional[str] = None,
    activity: Optional[str] = None,
    package: Optional[str] = None,
    quantity: Optional[int] = None,
    duration_min: Optional[int] = None,
    date_time_iso: Optional[str] = None,
    pickup_required: Optional[Any] = None,
    payment_method: Optional[str] = None,
    price_aed: Optional[float] = None,
    add_item: Optional[dict] = None,
    notes: Optional[Any] = None,
) -> str:
    """
    Update water booking draft fields. Supports two calling styles:
    1) water_booking_update(user_id="..", patch={...})
    2) water_booking_update(user_id="..", date_time_iso="..", quantity=2, ...)
    """
    draft = _get_or_create_water_booking(user_id)

    merged: Dict[str, Any] = {}
    if isinstance(patch, dict):
        merged.update(patch)

    direct = {
        "customer_name": customer_name,
        "activity": activity,
        "package": package,
        "quantity": quantity,
        "duration_min": duration_min,
        "date_time_iso": date_time_iso,
        "pickup_required": pickup_required,
        "payment_method": payment_method,
        "price_aed": price_aed,
        "add_item": add_item,
        "notes": notes,
    }
    for k, v in direct.items():
        if v is not None:
            merged[k] = v

    # Normalize all values
    for k in list(merged.keys()):
        if k != "add_item":
            merged[k] = _normalize_value(merged[k], k)

    def _ensure_items_from_draft() -> None:
        if draft.get("items") is None:
            draft["items"] = []
        if draft["items"]:
            return
        if any(draft.get(k) not in [None, ""] for k in ["activity", "package", "quantity", "duration_min", "date_time_iso"]):
            draft["items"].append({
                "activity": draft.get("activity"),
                "package": draft.get("package"),
                "quantity": draft.get("quantity"),
                "duration_min": draft.get("duration_min"),
                "date_time_iso": draft.get("date_time_iso"),
            })

    def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(item)
        for k in out:
            out[k] = _normalize_value(out[k], k)
        return out

    add_item_payload = merged.pop("add_item", None)
    if isinstance(add_item_payload, dict):
        _ensure_items_from_draft()
        item = _normalize_item(add_item_payload)
        draft["items"].append(item)
        for key in ["activity", "package", "quantity", "duration_min"]:
            if key in item and item[key] not in [None, ""]:
                draft[key] = item[key]
        if item.get("date_time_iso") and not draft.get("date_time_iso"):
            draft["date_time_iso"] = item.get("date_time_iso")

    # apply
    for k, v in merged.items():
        if k == "date_time_iso" and draft.get("items"):
            continue
        if k in draft:
            draft[k] = v
        elif k == "notes":
            if isinstance(v, list):
                draft["notes"].extend(v)
            else:
                draft["notes"].append(str(v))
    if "date_time_iso" in merged and draft.get("date_time_iso"):
        draft["booking_date"] = str(draft["date_time_iso"]).split("T")[0]

    # Store price (LLM already calculated with correct VAT if applicable)
    # DO NOT re-apply VAT here - the LLM has already handled it correctly in the breakdown
    if "price_aed" in merged and merged["price_aed"] is not None:
        # Store as base price only for recalculation if payment_method changes
        # Assume LLM provided the FINAL price (with VAT if card was chosen)
        final_price = float(merged["price_aed"])
        
        # Extract base price by checking current payment method
        # If card, divide by 1.05 to get base; otherwise, price IS the base
        current_payment = (draft.get("payment_method") or "").lower()
        if current_payment == "card":
            draft["price_aed_base"] = round(final_price / CARD_VAT_MULTIPLIER, 2)
        else:
            draft["price_aed_base"] = round(final_price, 2)
        
        draft["price_aed"] = final_price
    
    # RECALCULATE PRICE IF PAYMENT_METHOD CHANGED (use base price, reapply VAT logic)
    if "payment_method" in merged and draft.get("price_aed_base") is not None:
        base_price = draft["price_aed_base"]
        new_payment = str(merged["payment_method"]).lower().strip() if merged["payment_method"] else ""
        if new_payment == "card":
            draft["price_aed"] = round(base_price * CARD_VAT_MULTIPLIER, 2)
        else:
            draft["price_aed"] = round(base_price, 2)

    # IMMEDIATE TIME VALIDATION (fail fast)
    # Check if we just set duration_min or date_time_iso
    if any(k in merged for k in ["duration_min", "date_time_iso"]):
        time_error = _validate_time_immediately(draft)
        if time_error:
            # Return error without marking as ready - force agent to fix time first
            return json.dumps({"error": time_error, "draft": draft})

    item_keys = {"activity", "package", "quantity", "duration_min", "date_time_iso"}
    if draft.get("items"):
        current = draft["items"][-1]
        for k in item_keys:
            if k in merged:
                current[k] = merged[k]
        draft["items"][-1] = current

    def _items_complete() -> bool:
        items = draft.get("items") or []
        for item in items:
            activity_val = (item.get("activity") or "").strip().lower()
            if not activity_val:
                return False
            if item.get("quantity") in [None, ""]:
                return False
            if item.get("duration_min") in [None, ""]:
                return False
            if not (item.get("date_time_iso") or draft.get("date_time_iso")):
                return False
            if activity_val in {"jetski", "jet ski"} and not item.get("package"):
                return False
        return True

    if draft.get("items"):
        required = ["customer_name", "payment_method"]
        is_ready = _items_complete() and all(draft.get(r) not in [None, ""] for r in required)
    else:
        required = ["customer_name", "activity", "quantity", "duration_min", "date_time_iso", "payment_method"]
        is_ready = all(draft.get(r) not in [None, ""] for r in required)

    draft["status"] = "ready_to_confirm" if is_ready else "collecting"
    if any(k in merged for k in ["quantity", "duration_min", "pickup_required", "payment_method"]) or add_item_payload:
        draft["price_aed"] = None
    
    # Don't clear price if it's being explicitly set (e.g., after discount calculation)
    if "price_aed" in merged and merged["price_aed"] is not None:
        pass  # Keep the price that was set above
    
    WATER_BOOKINGS[user_id] = draft

    return json.dumps(draft)

@tool
def water_booking_compute_price(user_id: str) -> str:
    """
    Compute water booking price with precision handling.
    Always recalculates; never uses cached prices.
    """
    draft = _get_or_create_water_booking(user_id)
    draft["price_aed"] = None

    items = draft.get("items") or []
    
    # Validate completeness
    if items:
        for item in items:
            if item.get("duration_min") is None:
                return json.dumps({"error": "Missing duration for one of the activities.", "draft": draft})
            if item.get("quantity") is None:
                return json.dumps({"error": "Missing quantity for one of the activities.", "draft": draft})
            if not item.get("activity"):
                return json.dumps({"error": "Missing activity for one of the activities.", "draft": draft})
            if not (item.get("date_time_iso") or draft.get("date_time_iso")):
                return json.dumps({"error": "Missing booking time for one of the activities.", "draft": draft})
    else:
        if draft.get("date_time_iso") is None:
            return json.dumps({"error": "Missing booking time.", "draft": draft})
        if draft.get("duration_min") is None:
            return json.dumps({"error": "Missing duration.", "draft": draft})
        if draft.get("quantity") is None:
            return json.dumps({"error": "Missing quantity.", "draft": draft})

    # Validate times
    time_error = _validate_booking_times(draft)
    if time_error:
        return json.dumps({"error": time_error, "draft": draft})

    # Validate jet ski durations
    duration_error = _validate_jetski_duration(draft)
    if duration_error:
        return json.dumps({"error": duration_error, "draft": draft})

    return json.dumps({
        "needs_pricing_from_kb": True,
        "message": "Water pricing should be retrieved from the knowledge base.",
        "draft": draft
    })

@tool
def water_booking_confirm(user_id: str, final_price_aed: Optional[float] = None) -> str:
    """Confirm a water booking if the draft is complete and a price has been computed.
    
    Args:
        user_id: The user ID
        final_price_aed: Optional final price to save before confirming (e.g., after discount calculation)
    """
    draft = _get_or_create_water_booking(user_id)

    # If agent provides final price (e.g., after discount calculation), save it first
    if final_price_aed is not None:
        try:
            draft["price_aed"] = float(final_price_aed)
            WATER_BOOKINGS[user_id] = draft
        except Exception:
            pass

    if draft.get("status") != "ready_to_confirm":
        return json.dumps({"error": "Booking is not complete yet.", "draft": draft})

    if draft.get("price_aed") is None:
        return json.dumps({"error": "Missing price. Please compute from the knowledge base and update the booking.", "draft": draft})

    draft["status"] = "confirmed"
    WATER_BOOKINGS[user_id] = draft
    return json.dumps({
        "confirmed": True,
        "draft": draft
    })
# ----------------------------
@tool
def water_retrieval_tool(query: str, k: int = 6) -> str:
    """Search the Water Jetset knowledge base (Chroma) for factual answers and return top matching chunks."""
    return json.dumps(_water_search(query=query, k=k))

@tool
def water_about_tool() -> str:
    """Return 'About Water Jetset' information from the knowledge base."""
    return json.dumps(_water_search("About Water Jetset company overview", k=6))

@tool
def water_location_tool() -> str:
    """Return Water Jetset location/contact/map details from the knowledge base."""
    return json.dumps(_water_search("Water Jetset location address contact phone map", k=6))

@tool
def water_packages_tool(activity: str = "all", booking_date: Optional[str] = None) -> str:
    """Return packages/prices/durations from the knowledge base for water activities.
    
    Args:
        activity: jet ski, flyboard, jet car, or all
        booking_date: ISO date (2026-01-19) or DD-MM-YYYY for season-aware pricing
    """
    activity = (activity or "all").lower().strip()
    if activity not in {"jetski", "jet ski", "flyboard", "jetcar", "jet car", "all"}:
        activity = "all"
    if not booking_date:
        booking_date = _infer_booking_date_from_context()
    q = f"Packages prices durations for {activity} water activities Water Jetset Dubai"
    # Include booking date to help KB return correct season prices
    if booking_date:
        date_str = booking_date.split("T")[0] if "T" in booking_date else booking_date
        q += f" on {date_str}"
    return json.dumps(_water_search(q, k=8))

@tool
def water_faq_tool(question: str = "") -> str:
    """Return policy/FAQ answers from the knowledge base (age limits, rules, refunds, safety, etc.)."""
    # Expand question with common FAQ keywords for better semantic matching
    faq_keywords = [
        "age", "minimum age", "refund", "cancellation", "policy", 
        "payment", "safety", "rules", "requirements", "restrictions",
        "deposit", "insurance", "liability", "children", "pregnant",
        "health", "swimming", "experience", "training"
    ]
    
    # Check if question contains FAQ-related keywords
    question_lower = question.lower().strip()
    matched_keywords = [kw for kw in faq_keywords if kw in question_lower]
    
    # Build enhanced search query
    if matched_keywords:
        q = f"Water Jetset FAQ policies {' '.join(matched_keywords)}"
    else:
        q = f"Water Jetset FAQ policies rules {question_lower}" if question_lower else "Water Jetset FAQ policies rules"
    
    results = _water_search(q, k=10)  # Increased k for better coverage
    
    # If no good matches, try a broader search
    if not results.get("matches") or len(results["matches"]) < 3:
        results = _water_search("Water Jetset policies FAQ refunds age payment safety rules", k=10)
    
    return json.dumps(results)

def water_tools():
    return [
        water_current_datetime_tool,
        water_retrieval_tool,
        water_about_tool,
        water_location_tool,
        water_packages_tool,
        water_faq_tool,
        water_booking_get_or_create,
        water_booking_update,
        water_booking_compute_price,
        water_booking_confirm,
    ]

def has_active_water_booking(user_id: str) -> bool:
    status = WATER_BOOKINGS.get(user_id, {}).get("status")
    return status in {"collecting", "ready_to_confirm"}
