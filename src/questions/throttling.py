from rest_framework.throttling import AnonRateThrottle


class QuestionCreateThrottle(AnonRateThrottle):
    """
    Limit how fast a client can create questions (per IP).

    Each question triggers a Wikipedia call, and Wikipedia rate-limits us,
    so this protects the shared upstream quota. Rate is set by
    QUESTION_CREATE_RATE (default 10/minute).
    """

    scope = "question_create"
