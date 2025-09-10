# routes/mcq.py

from flask import Blueprint, request
from math import ceil
from mongoengine.errors import ValidationError, DoesNotExist

from utils.response import response
# reuse token_required from your other routes (adjust import path if needed)
from routes.test.tests import token_required

from models.questions.mcq import MCQ, MCQConfig

mcq_bp = Blueprint("mcq", __name__, url_prefix="/test/questions/mcqs")
def mcq_minimal_to_json(mcq: MCQ) -> dict:
    """
    Minimal representation used by list endpoints.
    Includes options in requested format.
    """
    options_json = []
    for o in mcq.options:
        options_json.append({
            "id": o.option_id,
            "text": o.value,
            "is_correct": o.option_id in mcq.correct_options
        })

    return {
        "id": str(mcq.id),
        "title": mcq.title,
        "question": mcq.question_text,
        "difficulty_level": mcq.difficulty_level,
        "topic": mcq.topic,
        "subtopic": mcq.subtopic,
        "tags": mcq.tags or [],
        "marks": mcq.marks,
        "time_limit": mcq.time_limit,
        "is_multiple": bool(mcq.is_multiple),
        "options": options_json,  # ✅ added
    }


@mcq_bp.route("/", methods=["GET"])
@token_required
def list_mcqs():
    """
    GET /mcqs
    Query params:
      - page (int, default 1)
      - per_page (int, default 20)
      - tags (comma separated, matches ANY tag)
      - topic (exact match)
      - subtopic (exact match)
      - difficulty_level (exact match: Easy|Medium|Hard)
      - search (optional text search against title/question_text)
      - sort_by (optional field name, default: created at / id)
      - sort_dir (asc|desc, default desc)

    Response:
      {
        success: true,
        message: "...",
        data: {
          items: [... minimal mcq ...],
          meta: {
            total: int,
            page: int,
            per_page: int,
            total_pages: int,
            topics: [...],
            subtopics: [...],
            tags: [...],
            difficulty_levels: [...]
          }
        }
      }
    """
    params = request.args

    # pagination
    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        per_page = int(params.get("per_page", 20))
        if per_page <= 0:
            per_page = 20
        # cap per_page to prevent abuse
        per_page = min(per_page, 200)
    except ValueError:
        per_page = 20

    # filters
    tags_param = params.get("tags")
    tags = [t.strip() for t in tags_param.split(",") if t.strip()] if tags_param else []

    topic = params.get("topic")
    subtopic = params.get("subtopic")
    difficulty_level = params.get("difficulty_level")

    search = params.get("search", "").strip()

    # sort
    sort_by = params.get("sort_by", None)  # e.g., "marks" or "difficulty_level"
    sort_dir = params.get("sort_dir", "desc").lower()
    sort_prefix = "-" if sort_dir == "desc" else ""

    # build query
    query = {}
    # tags: match any provided tag in the mcq.tags list
    if tags:
        query["tags__in"] = tags
    if topic:
        query["topic"] = topic
    if subtopic:
        query["subtopic"] = subtopic
    if difficulty_level:
        query["difficulty_level"] = difficulty_level

    try:
        # base queryset
        qs = MCQ.objects(**query)

        # basic search (title or question_text) - case-insensitive contains
        if search:
            # MongoEngine Q for OR
            from mongoengine.queryset.visitor import Q as MQ
            qs = qs.filter(MQ(title__icontains=search) | MQ(question_text__icontains=search))

        total = qs.count()

        # sorting: default by id (descending)
        if sort_by:
            # ensure not allowing arbitrary injection; only allow a whitelist
            allowed_sort_fields = {"marks", "difficulty_level", "time_limit", "title", "id"}
            if sort_by not in allowed_sort_fields:
                sort_by = "id"
            ordering = f"{sort_prefix}{sort_by}"
        else:
            ordering = "-id"  # newest first by default

        qs = qs.order_by(ordering)

        # pagination slicing (mongoengine supports skip/limit via [start:end])
        start = (page - 1) * per_page
        end = start + per_page
        items = list(qs[start:end])

        total_pages = ceil(total / per_page) if per_page else 1

        items_json = [mcq_minimal_to_json(m) for m in items]

        # meta: try to use MCQConfig document if available for canonical lists
        config = MCQConfig.objects.first()
        if config:
            topics = config.topics or []
            subtopics = config.subtopics or []
            tags_list = config.tags or []
            difficulty_levels = config.difficulty_levels or []
        else:
            # fallback: aggregate from MCQ collection
            # NOTE: these queries are simple and may be slow on large collections;
            # consider maintaining MCQConfig or indexes for production.
            topics = MCQ.objects.distinct("topic") or []
            subtopics = MCQ.objects.distinct("subtopic") or []
            tags_list = MCQ.objects.distinct("tags") or []
            difficulty_levels = MCQ.objects.distinct("difficulty_level") or []

        meta = {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "topics": sorted([t for t in topics if t]) ,           # remove falsy
            "subtopics": sorted([s for s in subtopics if s]),
            "tags": sorted([t for t in tags_list if t]),
            "difficulty_levels": sorted([d for d in difficulty_levels if d]),
        }

        data = {"items": items_json, "meta": meta}
        return response(True, "MCQs fetched", data), 200

    except ValidationError as e:
        return response(False, f"Invalid query: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Failed to fetch MCQs: {str(e)}"), 500

