from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from club.exceptions import RateLimitException, InvalidCode
from users.models.user import User
from utils.strings import random_string, random_number


class Apps(models.Model):
    id = models.CharField(max_length=16, primary_key=True)
    name = models.CharField(max_length=64, unique=True)
    owner = models.ForeignKey(User, related_name="apps", null=True, on_delete=models.CASCADE)
    jwt_secret = models.TextField()
    jwt_algorithm = models.CharField(max_length=16)
    jwt_expire_hours = models.IntegerField(default=240)
    redirect_urls = ArrayField(models.CharField(max_length=256), default=list, null=False)
    service_token = models.CharField(max_length=128, unique=True, db_index=True, null=True)

    class Meta:
        db_table = "apps"


class Session(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    user = models.ForeignKey(User, related_name="sessions", db_index=True, on_delete=models.CASCADE)
    app = models.ForeignKey(Apps, related_name="sessions", null=True, on_delete=models.CASCADE)

    token = models.CharField(max_length=128, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "sessions"

    @classmethod
    def create_for_user(cls, user):
        return Session.objects.create(
            user=user,
            token=random_string(length=32),
            created_at=datetime.utcnow(),
            expires_at=max(user.membership_expires_at, datetime.utcnow() + timedelta(days=30)),
        )


class Code(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    recipient = models.CharField(max_length=128, db_index=True)
    code = models.CharField(max_length=128, db_index=True)

    user = models.ForeignKey(User, related_name="codes", db_index=True, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True)

    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "codes"
        ordering = ["-created_at"]

    @classmethod
    def create_for_user(cls, user: User, recipient: str, length=6):
        recipient = recipient.lower()
        last_codes_count = Code.objects.filter(
            recipient=recipient,
            created_at__gte=datetime.utcnow() - settings.AUTH_MAX_CODE_TIMEDELTA,
        ).count()
        if last_codes_count >= settings.AUTH_MAX_CODE_COUNT:
            raise RateLimitException(title="Вы запросили слишком много кодов", message="Подождите немного")

        return Code.objects.create(
            recipient=recipient,
            user=user,
            code=random_number(length),
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + settings.AUTH_CODE_EXPIRATION_TIMEDELTA,
        )

    @classmethod
    def check_code(cls, recipient: str, code: str) -> User:
        recipient = recipient.lower()
        last_code = Code.objects.filter(recipient=recipient).order_by("-created_at").first()
        if not last_code:
            raise InvalidCode()

        if last_code.attempts >= settings.AUTH_MAX_CODE_ATTEMPTS:
            raise RateLimitException(
                title="Вы ввели код неправильно несколько раз. Придётся запросить его заново",
                message="Все прошлые коды больше недействительны ради защиты от перебора"
            )

        if last_code.is_expired() or last_code.code != code:
            last_code.attempts += 1
            last_code.save()
            raise InvalidCode()

        Code.objects.filter(recipient=recipient).delete()
        return last_code.user

    def is_expired(self):
        return self.expires_at <= datetime.utcnow()
