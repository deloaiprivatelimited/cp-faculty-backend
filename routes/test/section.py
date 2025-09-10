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

test_bp = Blueprint("section", __name__, url_prefix="/tests")

# add these imports near top of your routes file
from models.test.section import Section
from models.test.test import Test
from mongoengine.errors import DoesNotExist, ValidationError


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
# POST /tests/<test_id>/sections
@test_bp.route("/<test_id>/sections", methods=["POST"])
@token_required
def add_section_to_test(test_id):
    """
    Create a Section and attach it to a Test.
    Body: { "name": "...", "description": "...", "instructions": "...", "time_restricted": true|false }
    """
    data = request.get_json() or {}
    name = data.get("name")
    if not name:
        return response(False, "Section 'name' is required"), 400

    time_restricted = bool(data.get("time_restricted", False))
    description = data.get("description", "")
    instructions = data.get("instructions", "")  # new

    # ensure test exists
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # create section
    section = Section(
        name=name,
        description=description,
        instructions=instructions,      # new
        time_restricted=time_restricted
    )
    try:
        section.save()
    except (ValidationError, ValueError) as e:
        return response(False, f"Error creating section: {str(e)}"), 400

    # attach reference to appropriate list on Test
    if time_restricted:
        test.sections_time_restricted = (test.sections_time_restricted or []) + [section]
    else:
        test.sections_open = (test.sections_open or []) + [section]

    try:
        test.save()
    except Exception as e:
        # rollback created section if attaching fails (best-effort)
        try:
            section.delete()
        except Exception:
            pass
        return response(False, f"Error attaching section to test: {str(e)}"), 500

    return response(True, "Section created and attached", section.to_json()), 201


# PUT /sections/<section_id>
@test_bp.route("/sections/<section_id>", methods=["PUT"])
@token_required
def update_section(section_id):
    """
    Update a Section. Body may include: name, description, instructions, time_restricted
    If time_restricted flips, move references on Tests accordingly.
    """
    data = request.get_json() or {}
    try:
        section = Section.objects.get(id=section_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Section not found"), 404

    updated = False
    old_time_restricted = bool(section.time_restricted)

    if "name" in data and data["name"] is not None:
        section.name = data["name"]
        updated = True
    if "description" in data:
        # allow empty string
        section.description = data["description"] or ""
        updated = True
    if "instructions" in data:
        # allow empty string (clear instructions)
        section.instructions = data["instructions"] or ""
        updated = True
    if "time_restricted" in data:
        section.time_restricted = bool(data["time_restricted"])
        updated = True

    if not updated:
        return response(False, "No valid fields provided to update"), 400

    try:
        section.save()
    except (ValidationError, ValueError) as e:
        return response(False, f"Error updating section: {str(e)}"), 400

    # If time_restricted changed, move references in Tests
    new_time_restricted = bool(section.time_restricted)
    if old_time_restricted != new_time_restricted:
        try:
            if old_time_restricted:
                # was in time_restricted list, move to open
                tests_with_old = Test.objects(sections_time_restricted=section)
                for t in tests_with_old:
                    # remove from time_restricted
                    t.update(pull__sections_time_restricted=section)
                    # add to open (avoid duplicates)
                    t.update(push__sections_open=section)
            else:
                # was in open list, move to time_restricted
                tests_with_old = Test.objects(sections_open=section)
                for t in tests_with_old:
                    t.update(pull__sections_open=section)
                    t.update(push__sections_time_restricted=section)
        except Exception as e:
            # log and return partial success (section updated but moving refs failed)
            return response(False, f"Section updated but failed to move references: {str(e)}"), 500

    return response(True, "Section updated", section.to_json()), 200


# GET /tests/<test_id>/sections
@test_bp.route("/<test_id>/sections", methods=["GET"])
@token_required
def get_sections_by_test(test_id):
    """
    Return sections attached to a test, separated into time_restricted and open lists.
    Response data: { "sections_time_restricted": [...], "sections_open": [...] }
    """
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # gather ids from test (may be empty)
    time_ids = [s.id for s in (test.sections_time_restricted or [])]
    open_ids = [s.id for s in (test.sections_open or [])]

    # fetch Section documents in two queries
    sections_time = list(Section.objects(id__in=time_ids)) if time_ids else []
    sections_open = list(Section.objects(id__in=open_ids)) if open_ids else []

    # convert to json
    data = {
        "sections_time_restricted": [s.to_json() for s in sections_time],
        "sections_open": [s.to_json() for s in sections_open],
    }
    return response(True, "Sections fetched", data), 200


# Add these imports near top of your routes/test.py
from flask import jsonify
from mongoengine.errors import DoesNotExist, ValidationError
from models.test.section import Section, SectionQuestion  # Section & embedded wrapper
# Source MCQ: the 'questions' folder model (question bank)
from models.questions.mcq import MCQ as SourceMCQ
# Target MCQ: the test-specific MCQ model where duplicates should be stored
from models.test.questions.mcq import MCQ as TestMCQ, Option as TestOption

# POST /sections/<section_id>/select-mcqs
@test_bp.route("/sections/<section_id>/select-mcqs", methods=["POST"])
@token_required
def select_mcqs_for_section(section_id):
    """
    Duplicate MCQs from the question bank into test mcqs and add references
    to the given Section's questions list.

    Request JSON: { "question_ids": ["<src_id1>", "<src_id2>", ...] }
    """
    data = request.get_json() or {}
    question_ids = data.get("question_ids", [])
    if not isinstance(question_ids, list) or not question_ids:
        return response(False, "Provide a non-empty list 'question_ids'"), 400

    # load section
    try:
        section = Section.objects.get(id=section_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Section not found"), 404

    created = []
    failed = []

    # loop over provided source question ids
    for src_id in question_ids:
        print(src_id)
        new_mcq = None
        try:
            # fetch source MCQ from question bank
            src = SourceMCQ.objects.get(id=src_id)
        except (DoesNotExist, ValidationError):
            print('got ehre')
            failed.append({"source_id": src_id, "error": "Source MCQ not found"})
            continue

        try:
            # Build list of Embedded Option objects for target TestMCQ
            target_options = []
            for o in getattr(src, "options", []) or []:
                opt = TestOption(
                    option_id=getattr(o, "option_id", None) or None,
                    value=getattr(o, "value", "")
                )
                target_options.append(opt)

            # Prepare created_by metadata (use token payload if available)
            creator = {
                "id": getattr(request, "token_payload", {}).get("user_id", "system"),
                "name": getattr(request, "token_payload", {}).get("name", "System"),
            }

            # Create duplicate TestMCQ
            new_mcq = TestMCQ(
                title=getattr(src, "title", "") or "Untitled",
                question_text=getattr(src, "question_text", "") or "",
                options=target_options,
                correct_options=list(getattr(src, "correct_options", []) or []),
                is_multiple=bool(getattr(src, "is_multiple", False)),
                marks=getattr(src, "marks", 0) or 0,
                negative_marks=getattr(src, "negative_marks", 0) or 0,
                difficulty_level=getattr(src, "difficulty_level", None),
                explanation=getattr(src, "explanation", None),
                tags=list(getattr(src, "tags", []) or []),
                time_limit=getattr(src, "time_limit", None),
                topic=getattr(src, "topic", None),
                subtopic=getattr(src, "subtopic", None),
                created_by=creator
            )

            new_mcq.save()

            # create SectionQuestion embedded doc and append
            sq = SectionQuestion(
                question_type="mcq",
                mcq_ref=new_mcq,    # <-- USE mcq_ref now
                coding_ref=None,
                rearrange_ref=None
            )

            section.questions = (section.questions or []) + [sq]
            created.append({"source_id": src_id, "new_id": str(new_mcq.id)})

        except Exception as e:
            failed.append({"source_id": src_id, "error": str(e)})
            # attempt best-effort cleanup: if new_mcq exists, delete it
            try:
                if new_mcq is not None and getattr(new_mcq, "id", None):
                    new_mcq.delete()
            except Exception:
                pass
            continue

    # Save section once after processing all
    try:
        print(section.questions)
        section.save()
    except Exception as e:
        print(e)
        # If saving section fails, report error and (optionally) rollback created MCQs
        return response(False, f"Failed to attach questions to section: {str(e)}"), 500

    return response(True, "Questions duplicated and attached", {"created": created, "failed": failed}), 200

# routes/test.py
# GET /sections/<section_id>/questions
@test_bp.route("/sections/<section_id>/questions", methods=["GET"])
@token_required
def get_section_questions(section_id):
    """
    Fetch all questions (with details) for a given Section.

    Response:
    {
      "questions": [
        {
          "type": "mcq",
          "question": {...full mcq json...}
        },
        ...
      ]
    }
    """
    try:
        section = Section.objects.get(id=section_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Section not found"), 404

    results = []

    for sq in section.questions or []:
        try:
            if sq.question_type == "mcq" and getattr(sq, "mcq_ref", None):
                results.append({
                    "type": "mcq",
                    "question": sq.mcq_ref.to_json() if hasattr(sq.mcq_ref, "to_json") else None
                })
            elif sq.question_type == "coding" and getattr(sq, "coding_ref", None):
                results.append({
                    "type": "coding",
                    "question": sq.coding_ref.to_json() if hasattr(sq.coding_ref, "to_json") else None
                })
            elif sq.question_type == "rearrange" and getattr(sq, "rearrange_ref", None):
                results.append({
                    "type": "rearrange",
                    "question": sq.rearrange_ref.to_json() if hasattr(sq.rearrange_ref, "to_json") else None
                })
            else:
                # If the declared type is present but corresponding ref is null, return null question
                results.append({
                    "type": sq.question_type,
                    "question": None
                })
        except Exception as e:
            results.append({
                "type": sq.question_type,
                "error": str(e)
            })

    return response(True, "Questions fetched", {"questions": results}), 200


# ---------------------------
# Update MCQ by ID (full replace)
# ---------------------------
@test_bp.route('/mcq/edit/<string:mcq_id>', methods=['PUT'])
@token_required
def update_mcq(mcq_id):
    from models.test.questions.mcq import MCQ
    try:
        mcq = MCQ.objects.get(id=mcq_id)
        data = request.get_json() or {}

        # --- normalize options into EmbeddedDocument Option objects ---
        options_in = data.get('options', [])
        if not isinstance(options_in, list) or len(options_in) < 2:
            return response(False, 'At least two options are required'), 400

        from models.test.questions.mcq import Option  # ensure correct import path
        import uuid as _uuid
        normalized_options = []
        for opt in options_in:
            # accept both {option_id, value} or {value}
            val = (opt.get('value') if isinstance(opt, dict) else str(opt)).strip()
            if not val:
                return response(False, 'Option values cannot be empty'), 400
            oid = (opt.get('option_id') if isinstance(opt, dict) else None) or str(_uuid.uuid4())
            normalized_options.append(Option(option_id=oid, value=val))

        # --- correct options ---
        is_multiple = bool(data.get('is_multiple', False))
        correct_ids = data.get('correct_options') or []
        # Fallbacks for create/edit clients that send by value or index
        if not correct_ids:
            by_values = data.get('correct_option_values') or []
            if by_values:
                map_by_val = {o.value: o.option_id for o in normalized_options}
                correct_ids = [map_by_val[v] for v in by_values if v in map_by_val]
        if not correct_ids:
            by_indexes = data.get('correct_option_indexes') or []
            if by_indexes:
                for i in by_indexes:
                    try:
                        correct_ids.append(normalized_options[int(i)].option_id)
                    except Exception:
                        pass

        option_ids_set = {o.option_id for o in normalized_options}
        if not correct_ids:
            return response(False, 'Select at least one correct option'), 400
        if not all(cid in option_ids_set for cid in correct_ids):
            print('yes')
            return response(False, 'correct_options contain unknown IDs'), 400
        if not is_multiple and len(correct_ids) > 1:
            return response(False, 'Multiple correct not allowed when is_multiple is false'), 400

        # --- assign to document ---
        mcq.title = data.get('title', mcq.title)
        mcq.question_text = data.get('question_text', mcq.question_text)
        mcq.options = normalized_options
        mcq.correct_options = correct_ids
        mcq.is_multiple = is_multiple
        mcq.marks = float(data.get('marks', mcq.marks))
        mcq.negative_marks = float(data.get('negative_marks', mcq.negative_marks))
        mcq.difficulty_level = data.get('difficulty_level', mcq.difficulty_level)
        mcq.explanation = data.get('explanation', mcq.explanation)
        mcq.tags = data.get('tags', mcq.tags) or []
        mcq.time_limit = int(data.get('time_limit', mcq.time_limit or 60))
        mcq.topic = data.get('topic', mcq.topic)
        mcq.subtopic = data.get('subtopic', mcq.subtopic)

        mcq.save()
        return response(True, 'MCQ updated successfully', mcq.to_json()), 200

    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError as ve:
        return response(False, f'Validation error: {ve}'), 400
    except Exception as e:
        print(e)
        return response(False, f'Error: {str(e)}'), 500

@test_bp.route('/mcq/<string:mcq_id>', methods=['GET'])
@token_required
def get_mcq(mcq_id):
    from models.test.questions.mcq import MCQ
    try:
        mcq = MCQ.objects.get(id=mcq_id)
        return response(True, 'MCQ fetched', mcq.to_json()), 200
    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError:
        return response(False, 'Invalid MCQ ID'), 400
    except Exception as e:
        print(e)
        return response(False, f'Error: {str(e)}'), 500
