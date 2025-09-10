# models/test.py
from datetime import datetime
from mongoengine import (
    Document,
    StringField,
    DateTimeField,
    ListField,
    DictField,
    ReferenceField,
    NULLIFY, PULL
)
from models.test.section import Section

# ensure Section class is importable here
# avoid circular import by using the string name 'Section' in ReferenceField

class Test(Document):
    """Model for Tests"""

    test_name = StringField(required=True)
    description = StringField()

    start_datetime = DateTimeField(required=True)
    end_datetime = DateTimeField(required=True)

    instructions = StringField()  # rich text

    tags = ListField(StringField())
    created_by = DictField(required=True, default=lambda: {"id": "system", "name": "System"})
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    # New: two separate lists for sections
    sections_time_restricted = ListField(ReferenceField("Section", reverse_delete_rule=PULL))
    sections_open = ListField(ReferenceField("Section", reverse_delete_rule=PULL))

    meta = {"collection": "tests", "indexes": ["start_datetime", "end_datetime", "test_name"]}

    def clean(self):
        """Validation before saving"""
        if self.start_datetime and self.end_datetime:
            if self.start_datetime >= self.end_datetime:
                raise ValueError("start_datetime must be earlier than end_datetime")

    def save(self, *args, **kwargs):
        """Auto-update timestamps and run validation"""
        self.clean()
        self.updated_at = datetime.utcnow()
        if not self.created_at:
            self.created_at = datetime.utcnow()
        return super(Test, self).save(*args, **kwargs)

    def to_json(self):
        """Convert Test document to dict/JSON"""
        return {
            "id": str(self.id),
            "test_name": self.test_name,
            "description": self.description,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
            "instructions": self.instructions,
            "tags": self.tags,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            # include sections if you want (convert refs to ids)
            "sections_time_restricted": [str(s.id) for s in (self.sections_time_restricted or [])],
            "sections_open": [str(s.id) for s in (self.sections_open or [])],
        }

    def to_minimal_json(self):
        return {
            "id": str(self.id),
            "test_name": self.test_name,
            "description": self.description,
            "notes": self.instructions,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
        }
