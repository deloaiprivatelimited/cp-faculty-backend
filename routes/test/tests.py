# routes/test.py

from flask import Blueprint, request
from mongoengine.errors import ValidationError, NotUniqueError
from datetime import datetime

from utils.response import response
from utils.jwt import verify_access_token
from models.test.test import Test
from math import ceil
from mongoengine import Q
from datetime import datetime

test_bp = Blueprint("test", __name__, url_prefix="/tests")


def token_required(f):
    """Decorator to protect routes using Authorization: Bearer <token>"""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", None)
        if not auth_header or not auth_header.startswith("Bearer "):
            return response(False, "Authorization header missing or malformed"), 401

        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = verify_access_token(token)
        except ValueError as e:
            return response(False, str(e)), 401

        request.token_payload = payload
        return f(*args, **kwargs)

    return decorated
def _parse_pagination_args():
    """Helper to parse page/per_page query params. Returns (page, per_page, error_message)"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        if page < 1 or per_page < 1:
            return None, None, "page and per_page must be positive integers"
    except ValueError:
        return None, None, "page and per_page must be integers"
    return page, per_page, None


def _apply_search(qs):
    """Apply search query 'q' filtering across test_name, description, instructions."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return qs
    # Case-insensitive partial match on name/description/instructions
    search_filter = Q(test_name__icontains=q) | Q(description__icontains=q) | Q(instructions__icontains=q)
    return qs.filter(search_filter)


def _paginate_and_respond(qs, page, per_page, sort=None):
    """Apply sorting, pagination and return the response dict."""
    total = qs.count()
    if sort:
        qs = qs.order_by(sort)
    else:
        qs = qs.order_by("start_datetime")

    total_pages = ceil(total / per_page) if per_page else 1
    skip = (page - 1) * per_page
    qs = qs.skip(skip).limit(per_page)

    tests = [t.to_minimal_json() for t in qs]
    meta = {"total": total, "page": page, "per_page": per_page, "total_pages": total_pages}
    return response(True, "OK", data={"tests": tests, "meta": meta}), 200


@test_bp.route("/add", methods=["POST"])
@token_required
def add_test():
    """
    POST /tests/add
    Protected route.
    Body: {
        "test_name" or "name": "...",
        "description": "...",
        "start_datetime" or "startDateTime": "2025-09-09T10:00:00",
        "end_datetime" or "endDateTime": "2025-09-09T12:00:00",
        "instructions": "<p>Rich text allowed</p>",
        "tags": ["python", "arrays"]
    }
    """
    data = request.get_json() or {}
    print(data)

    # Accept either naming style (frontend sometimes uses camelCase)
    test_name = data.get("test_name") or data.get("name")
    start_datetime = data.get("start_datetime") or data.get("startDateTime")
    end_datetime = data.get("end_datetime") or data.get("endDateTime")

    if not test_name or not start_datetime or not end_datetime:
        return response(False, "test_name, start_datetime and end_datetime are required"), 400

    try:
        start = datetime.fromisoformat(start_datetime)
        end = datetime.fromisoformat(end_datetime)
    except Exception:
        return response(False, "Invalid datetime format, use ISO 8601"), 400

    # quick sanity: ensure start < end before creating the model (optional but clearer)
    if start >= end:
        return response(False, "start_datetime must be earlier than end_datetime"), 400

    payload = getattr(request, "token_payload", {})
    created_by = {
        "id": payload.get("admin_id", "system"),
        "name": payload.get("role", "college_admin"),
    }

    test = Test(
        test_name=test_name,
        description=data.get("description"),
        start_datetime=start,
        end_datetime=end,
        instructions=data.get("instructions"),
        tags=data.get("tags", []),
        created_by=created_by,
    )

    # Call clean explicitly to surface validation errors early (clean also runs inside save)
    try:
        test.clean()
    except ValueError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400

    try:
        test.save()
    except (ValidationError, NotUniqueError, ValueError) as e:
        # ValueError can still be raised from clean() inside save or other places
        return response(False, f"Error saving test: {str(e)}"), 400
    except Exception as e:
        # catch-all so we don't leak a 500 without helpful message
        return response(False, f"Unexpected error saving test: {str(e)}"), 500

    return response(True, "Test created successfully"), 201

from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
from bson import ObjectId
@test_bp.route("", methods=["GET"])
@token_required
def get_all_tests():
    """
    GET /tests
    Query params:
      - q: optional search string (matches name/description/notes)
      - page: page number (1-based)
      - per_page: items per page
      - sort: optional mongoengine order_by string (e.g. "-start_datetime")
    """
    page, per_page, err = _parse_pagination_args()
    if err:
        return response(False, err), 400

    qs = Test.objects  # no time filtering
    qs = _apply_search(qs)

    sort = request.args.get("sort")
    return _paginate_and_respond(qs, page, per_page, sort)


@test_bp.route("/past", methods=["GET"])
@token_required
def get_past_tests():
    """
    GET /tests/past
    Tests whose end_datetime < now (already finished). Newest finished first by default.
    Supports same query params as /tests
    """
    page, per_page, err = _parse_pagination_args()
    if err:
        return response(False, err), 400

    now = datetime.utcnow()
    qs = Test.objects(end_datetime__lt=now)
    qs = _apply_search(qs)

    # default sort: newest finished first
    sort = request.args.get("sort") or "-end_datetime"
    return _paginate_and_respond(qs, page, per_page, sort)


@test_bp.route("/ongoing", methods=["GET"])
@token_required
def get_ongoing_tests():
    """
    GET /tests/ongoing
    Tests where start_datetime <= now <= end_datetime
    """
    page, per_page, err = _parse_pagination_args()
    if err:
        return response(False, err), 400

    now = datetime.utcnow()
    qs = Test.objects(start_datetime__lte=now, end_datetime__gte=now)
    qs = _apply_search(qs)

    # default sort: soonest starting first
    sort = request.args.get("sort") or "start_datetime"
    return _paginate_and_respond(qs, page, per_page, sort)


@test_bp.route("/upcoming", methods=["GET"])
@token_required
def get_upcoming_tests():
    """
    GET /tests/upcoming
    Tests whose start_datetime > now (future tests)
    """
    page, per_page, err = _parse_pagination_args()
    if err:
        return response(False, err), 400

    now = datetime.utcnow()
    qs = Test.objects(start_datetime__gt=now)
    qs = _apply_search(qs)

    # default sort: earliest upcoming first
    sort = request.args.get("sort") or "start_datetime"
    return _paginate_and_respond(qs, page, per_page, sort)


# GET /tests/<id>
@test_bp.route("/<test_id>", methods=["GET"])
@token_required
def get_test(test_id):
    """
    GET /tests/<test_id>
    """
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404
    return response(True, "Test fetched", test.to_json()), 200


# PUT /tests/<id>
@test_bp.route("/<test_id>", methods=["PUT"])
@token_required
def update_test(test_id):
    """
    PUT /tests/<test_id>
    Body may include any of:
      "test_name", "description", "start_datetime", "end_datetime",
      "instructions", "tags"
    """
    data = request.get_json() or {}
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # Only allow updating safe fields
    allowed_fields = {"test_name", "description", "start_datetime", "end_datetime", "instructions", "tags"}
    updated = False

    # handle datetimes if provided
    if "start_datetime" in data and data["start_datetime"] is not None:
        try:
            test.start_datetime = datetime.fromisoformat(data["start_datetime"])
            updated = True
        except Exception:
            return response(False, "Invalid start_datetime format, use ISO 8601"), 400

    if "end_datetime" in data and data["end_datetime"] is not None:
        try:
            test.end_datetime = datetime.fromisoformat(data["end_datetime"])
            updated = True
        except Exception:
            return response(False, "Invalid end_datetime format, use ISO 8601"), 400

    for key in ("test_name", "description", "instructions", "tags"):
        if key in data:
            setattr(test, key, data.get(key))
            updated = True

    if not updated:
        return response(False, "No valid fields provided to update"), 400

    # Optionally record who updated it
    payload = getattr(request, "token_payload", {})
    updated_by = {"id": payload.get("admin_id", "system"), "name": payload.get("role", "college_admin")}
    # store updated_by inside created_by? If you want an audit trail add a separate field.
    # For now we won't overwrite created_by, but you may extend the model later.

    try:
        test.save()
    except (ValidationError, NotUniqueError, ValueError) as e:
        return response(False, f"Error updating test: {str(e)}"), 400

    return response(True, "Test updated", test.to_json()), 200


# DELETE /tests/<id>
@test_bp.route("/<test_id>", methods=["DELETE"])
@token_required
def delete_test(test_id):
    """
    DELETE /tests/<test_id>
    """
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    try:
        test.delete()
    except Exception as e:
        return response(False, f"Error deleting test: {str(e)}"), 400

    return response(True, "Test deleted"), 200



