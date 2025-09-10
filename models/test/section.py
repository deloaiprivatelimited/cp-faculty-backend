# models/section.py
from datetime import datetime
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, BooleanField, DateTimeField,
    ReferenceField, ListField, EmbeddedDocumentField, IntField
)

from models.test.questions.mcq import MCQ
# (later you’ll import CodingQuestion, RearrangeQuestion when models exist)
# from models.test.questions.coding import CodingQuestion
# from models.test.questions.rearrange import RearrangeQuestion


class SectionQuestion(EmbeddedDocument):
    """Wrapper for any question type inside a Section"""
    question_type = StringField(
        required=True,
        choices=["mcq", "coding", "rearrange"]
    )

    # Keep three separate references — only one is expected to be non-null
    mcq_ref = ReferenceField(MCQ, null=True)
    coding_ref = ReferenceField('CodingQuestion', null=True)      # placeholder
    rearrange_ref = ReferenceField('RearrangeQuestion', null=True)  # placeholder


class Section(Document):
    """Model for a Test Section"""
    name = StringField(required=True)
    description = StringField(default="")
    instructions = StringField(default="")
    time_restricted = BooleanField(default=False, required=True)

    questions = ListField(EmbeddedDocumentField(SectionQuestion), default=list)

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {"collection": "sections", "indexes": ["time_restricted", "name"]}

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description or "",
            "instructions": self.instructions or "",
            "time_restricted": self.time_restricted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
