from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.response import Response

from questions.models.question import Question
from questions.pagination import QuestionPagination
from questions.serializers import QuestionSerializer
from questions.tasks import process_question_task
from questions.throttling import QuestionCreateThrottle


class QuestionViewSet(viewsets.ModelViewSet):
    # Newest first. Ordering by the primary key matches creation order and
    # uses the existing PK index, so no extra index is needed. A stable
    # ordering is required for pagination to be deterministic.
    queryset = Question.objects.prefetch_related("source_documents").order_by("-id")
    serializer_class = QuestionSerializer
    pagination_class = QuestionPagination

    http_method_names = ["get", "post", "head", "options"]

    def get_throttles(self):
        # Throttle creation only; clients poll GET while waiting for answers.
        if self.action == "create":
            return [QuestionCreateThrottle()]
        return []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            question = serializer.save()

            transaction.on_commit(
                lambda question_id=question.pk: process_question_task.apply_async(
                    args=[question_id],
                )
            )

        return Response(
            self.get_serializer(question).data,
            status=status.HTTP_202_ACCEPTED,
        )
