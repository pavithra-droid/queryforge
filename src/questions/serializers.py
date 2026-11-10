from rest_framework import serializers

from questions.models import Question, SourceDocument


class SourceDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceDocument
        fields = ["title", "url"]


QUESTION_MAX_LENGTH = 500


class QuestionSerializer(serializers.ModelSerializer):
    # Bounded so a huge payload can't become an oversized Wikipedia search
    # URL or LLM prompt. Whitespace is trimmed, so "   " counts as blank.
    text = serializers.CharField(max_length=QUESTION_MAX_LENGTH)
    error_message = serializers.SerializerMethodField()
    source_documents = SourceDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = [
            "id",
            "text",
            "answer",
            "status",
            "error_message",
            "source_documents",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "answer",
            "status",
            "error_message",
            "source_documents",
            "created_at",
            "updated_at",
        ]

    def get_error_message(self, obj):
        if obj.status == Question.Status.FAILED:
            return obj.error_message
        return None
