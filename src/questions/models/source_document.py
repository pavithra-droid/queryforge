from django.db import models

from questions.models.question import Question


class SourceDocument(models.Model):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="source_documents"
    )
    title = models.CharField(max_length=255)
    url = models.URLField()
    content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
