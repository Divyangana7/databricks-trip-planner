"""
The agent: a tool-calling loop over the Foundation Model API chat endpoint.

The model plans in natural language and calls tools; we execute each tool against
Lakebase and feed the result back, until the model produces a final answer. The
returned `steps` log every tool call + result, which is the evidence that the
agent both retrieves (reads) and takes real actions (writes).
"""

import json
import logging

import models
from agent import tools as T

logger = logging.getLogger("trip-planner.agent")

SYSTEM_PROMPT = (
    "You are a weather-aware trip-planning assistant. You help users build and "
    "adjust day-by-day itineraries using live weather and air-quality data.\n"
    "Use the tools to read the trip, its weather, and activities, and to make "
    "real changes (add/move/remove itinerary items, build packing lists, "
    "reschedule outdoor activities when rain or poor air quality is forecast).\n"
    "Always prefer retrieving real data with tools over guessing. When you change "
    "the plan for weather reasons, state the specific reason (e.g. the AQI value "
    "or rain probability). When adding an itinerary item, call add_itinerary_item "
    "exactly once for that item, including its time in the same call. Be concise "
    "and end with a short summary of what you did."
)

# --- OpenAI-style tool schemas ---------------------------------------------
TOOLS = [
    {"type": "function", "function": {
        "name": "get_trip", "description": "Get a trip's dates, destination, and status.",
        "parameters": {"type": "object", "properties": {"trip_id": {"type": "integer"}},
                       "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "weather_by_day",
        "description": "Per-day weather + air-quality summary for the trip, with a bad-day flag and reason.",
        "parameters": {"type": "object", "properties": {"trip_id": {"type": "integer"}},
                       "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "list_itinerary", "description": "List the trip's current itinerary items.",
        "parameters": {"type": "object", "properties": {"trip_id": {"type": "integer"}},
                       "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "search_activities",
        "description": "Semantic search for activities at the trip's destination that match a query.",
        "parameters": {"type": "object", "properties": {
            "trip_id": {"type": "integer"},
            "query": {"type": "string", "description": "what the user is looking for"},
            "top_k": {"type": "integer", "default": 5},
            "indoor_only": {"type": "boolean", "default": False}},
            "required": ["trip_id", "query"]}}},
    {"type": "function", "function": {
        "name": "generate_itinerary",
        "description": "Create a full day-by-day itinerary (weather-appropriate, interest-matched). Replaces existing items by default.",
        "parameters": {"type": "object", "properties": {
            "trip_id": {"type": "integer"},
            "activities_per_day": {"type": "integer", "default": 3},
            "replace": {"type": "boolean", "default": True}},
            "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "reschedule_outdoor_for_weather",
        "description": "Move outdoor items off rainy/poor-AQI days to a good day, or swap them for an indoor activity if no good day exists. Records the reason on each change.",
        "parameters": {"type": "object", "properties": {"trip_id": {"type": "integer"}},
                       "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "build_packing_list",
        "description": "Generate and store a weather- and activity-aware packing list for the trip.",
        "parameters": {"type": "object", "properties": {"trip_id": {"type": "integer"}},
                       "required": ["trip_id"]}}},
    {"type": "function", "function": {
        "name": "add_itinerary_item", "description": "Add one itinerary item.",
        "parameters": {"type": "object", "properties": {
            "trip_id": {"type": "integer"}, "day_date": {"type": "string", "description": "YYYY-MM-DD"},
            "title": {"type": "string"}, "activity_id": {"type": "integer"},
            "start_time": {"type": "string"}, "end_time": {"type": "string"},
            "notes": {"type": "string"}},
            "required": ["trip_id", "day_date", "title"]}}},
    {"type": "function", "function": {
        "name": "move_itinerary_item", "description": "Move/reschedule an itinerary item to a new day/time.",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "integer"}, "day_date": {"type": "string"},
            "start_time": {"type": "string"}, "end_time": {"type": "string"},
            "reason": {"type": "string"}},
            "required": ["item_id"]}}},
    {"type": "function", "function": {
        "name": "remove_itinerary_item", "description": "Delete an itinerary item.",
        "parameters": {"type": "object", "properties": {"item_id": {"type": "integer"}},
                       "required": ["item_id"]}}},
    {"type": "function", "function": {
        "name": "add_packing_item", "description": "Add or update one packing-list item.",
        "parameters": {"type": "object", "properties": {
            "trip_id": {"type": "integer"}, "item_name": {"type": "string"},
            "category": {"type": "string"}, "quantity": {"type": "integer", "default": 1},
            "reason": {"type": "string"}},
            "required": ["trip_id", "item_name"]}}},
]

_DISPATCH = {
    "get_trip": T.get_trip,
    "weather_by_day": T.weather_by_day,
    "list_itinerary": T.list_itinerary,
    "search_activities": T.search_activities,
    "generate_itinerary": T.generate_itinerary,
    "reschedule_outdoor_for_weather": T.reschedule_outdoor_for_weather,
    "build_packing_list": T.build_packing_list,
    "add_itinerary_item": T.add_itinerary_item,
    "move_itinerary_item": T.move_itinerary_item,
    "remove_itinerary_item": T.remove_itinerary_item,
    "add_packing_item": T.add_packing_item,
}

# Tools that take a trip_id — used to auto-inject the active trip if the model omits it.
_TRIP_TOOLS = {"get_trip", "weather_by_day", "list_itinerary", "search_activities",
               "generate_itinerary", "reschedule_outdoor_for_weather",
               "build_packing_list", "add_itinerary_item", "add_packing_item"}


def _assistant_to_dict(msg) -> dict:
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return d


def _dispatch(name: str, args: dict):
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except Exception as exc:  # surface tool errors back to the model
        logger.exception("tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


def run_agent(user_message: str, trip_id: int | None = None, max_steps: int = 6) -> dict:
    """Run the tool-calling loop. Returns {'reply': str, 'steps': [tool calls...]}"""
    system = SYSTEM_PROMPT
    if trip_id is not None:
        system += f"\n\nThe active trip_id is {trip_id}. Use it unless the user names another."
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_message}]
    steps = []

    for _ in range(max_steps):
        resp = models.chat(messages, tools=TOOLS, tool_choice="auto", max_tokens=1024)
        msg = resp.choices[0].message
        messages.append(_assistant_to_dict(msg))

        if not msg.tool_calls:
            return {"reply": msg.content, "steps": steps}

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if trip_id is not None and name in _TRIP_TOOLS and "trip_id" not in args:
                args["trip_id"] = trip_id
            result = _dispatch(name, args)
            steps.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)})

    # Ran out of steps — ask for a final summary without more tools.
    resp = models.chat(messages, max_tokens=512)
    return {"reply": resp.choices[0].message.content, "steps": steps}
