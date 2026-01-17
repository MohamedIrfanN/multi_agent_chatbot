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
# Booking Store (separate from desert)
# ----------------------------
WATER_BOOKINGS: Dict[str, Dict[str, Any]] = {}
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
            "notes": []
        }
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
        "notes": notes,
    }
    for k, v in direct.items():
        if v is not None:
            merged[k] = v

    # normalize
    if "pickup_required" in merged:
        merged["pickup_required"] = _normalize_bool(merged["pickup_required"])

    if "activity" in merged and merged["activity"]:
        merged["activity"] = str(merged["activity"]).strip().lower()

    if "payment_method" in merged and merged["payment_method"]:
        merged["payment_method"] = str(merged["payment_method"]).strip().lower()

    for key in ["quantity", "duration_min"]:
        if key in merged and merged[key] is not None:
            try:
                merged[key] = int(merged[key])
            except Exception:
                pass

    if "price_aed" in merged and merged["price_aed"] is not None:
        try:
            merged["price_aed"] = float(merged["price_aed"])
        except Exception:
            pass

    # apply
    for k, v in merged.items():
        if k in draft:
            draft[k] = v
        elif k == "notes":
            if isinstance(v, list):
                draft["notes"].extend(v)
            else:
                draft["notes"].append(str(v))

    required = ["customer_name", "activity", "quantity", "duration_min", "date_time_iso", "payment_method"]
    is_ready = all(draft.get(r) not in [None, ""] for r in required)

    draft["status"] = "ready_to_confirm" if is_ready else "collecting"
    if any(k in merged for k in ["quantity", "duration_min", "pickup_required", "payment_method"]):
        draft["price_aed"] = None
    WATER_BOOKINGS[user_id] = draft

    return json.dumps(draft)

@tool
def water_booking_compute_price(user_id: str) -> str:
    """
    Placeholder for water pricing.
    Pricing must be retrieved from the water KB.
    """
    draft = _get_or_create_water_booking(user_id)

    if draft.get("price_aed") is not None:
        return json.dumps({"price_aed": draft["price_aed"], "draft": draft})

    if draft.get("date_time_iso") is None:
        return json.dumps({"error": "Missing booking time.", "draft": draft})

    if draft.get("duration_min") is None:
        return json.dumps({"error": "Missing duration.", "draft": draft})

    if draft.get("quantity") is None:
        return json.dumps({"error": "Missing quantity.", "draft": draft})

    try:
        start_dt = datetime.fromisoformat(draft["date_time_iso"])
    except Exception:
        return json.dumps({"error": "Invalid booking time format.", "draft": draft})

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ZoneInfo(TZ))
    else:
        start_dt = start_dt.astimezone(ZoneInfo(TZ))

    end_dt = start_dt + timedelta(minutes=int(draft["duration_min"]))
    opening_start = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    opening_end = start_dt.replace(hour=19, minute=0, second=0, microsecond=0)
    if start_dt < opening_start or end_dt > opening_end:
        return json.dumps({
            "error": "Booking time must start after 9am and finish by 7pm. Please choose a time that fits the full duration.",
            "draft": draft
        })

    if draft.get("activity") in {"jetski", "jet ski"}:
        base = _jetski_base_duration(draft.get("package"))
        duration = draft.get("duration_min")
        if base and duration % base != 0:
            return json.dumps({
                "error": f"Invalid duration for {draft.get('package')}. Must be a multiple of {base} minutes.",
                "draft": draft
            })

    return json.dumps({
        "needs_pricing_from_kb": True,
        "message": "Water pricing should be retrieved from the knowledge base.",
        "draft": draft
    })

@tool
def water_booking_confirm(user_id: str) -> str:
    """Confirm a water booking if the draft is complete and a price has been computed."""
    draft = _get_or_create_water_booking(user_id)

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
def water_packages_tool(activity: str = "all") -> str:
    """Return packages/prices/durations from the knowledge base for water activities."""
    activity = (activity or "all").lower().strip()
    if activity not in {"jetski", "jet ski", "flyboard", "jetcar", "jet car", "all"}:
        activity = "all"
    q = f"Packages prices durations for {activity} water activities Water Jetset Dubai"
    return json.dumps(_water_search(q, k=8))

@tool
def water_faq_tool(question: str = "") -> str:
    """Return policy/FAQ answers from the knowledge base (age limits, rules, refunds, safety, etc.)."""
    q = "Water Jetset FAQ policies rules"
    if question.strip():
        q = f"{q}. User question: {question}"
    return json.dumps(_water_search(q, k=8))

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
