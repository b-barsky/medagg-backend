from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Sum
from rest_framework import serializers

from apps.users.models import Group, User, UserProfile


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "date_joined",
        )
        read_only_fields = fields


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ("id", "name")
        read_only_fields = fields


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = (
            "organization",
            "job_title",
            "location",
            "website",
            "bio",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")


class CurrentUserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    profile = UserProfileSerializer(required=False)
    stats = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "is_staff",
            "date_joined",
            "last_login",
            "profile",
            "stats",
        )
        read_only_fields = (
            "id",
            "username",
            "display_name",
            "is_staff",
            "date_joined",
            "last_login",
            "stats",
        )

    @staticmethod
    def get_display_name(instance: User) -> str:
        full_name = instance.get_full_name().strip()
        return full_name or instance.get_username()

    @staticmethod
    def get_stats(instance: User) -> dict[str, int]:
        from apps.datasets.models import (
            DatasetImportRequester,
            DatasetMembership,
        )

        membership_stats = DatasetMembership.objects.filter(
            user=instance
        ).aggregate(
            dataset_count=Count("id"),
            total_dataset_bytes=Sum("dataset__size_bytes"),
        )

        return {
            "dataset_count": membership_stats["dataset_count"] or 0,
            "import_request_count": (
                DatasetImportRequester.objects.filter(user=instance).count()
            ),
            "total_dataset_bytes": (
                membership_stats["total_dataset_bytes"] or 0
            ),
        }

    def validate_email(self, value: str) -> str:
        normalized = value.strip().lower()

        if not normalized:
            return ""

        queryset = User.objects.filter(email__iexact=normalized)

        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )

        return normalized

    @transaction.atomic
    def update(self, instance: User, validated_data):
        profile_data = validated_data.pop("profile", None)
        changed_user_fields: list[str] = []

        for field, value in validated_data.items():
            if getattr(instance, field) != value:
                setattr(instance, field, value)
                changed_user_fields.append(field)

        if changed_user_fields:
            instance.save(update_fields=tuple(changed_user_fields))

        profile, _ = UserProfile.objects.get_or_create(user=instance)

        if profile_data is not None:
            changed_profile_fields: list[str] = []

            for field, value in profile_data.items():
                if getattr(profile, field) != value:
                    setattr(profile, field, value)
                    changed_profile_fields.append(field)

            if changed_profile_fields:
                profile.save()

        return instance

    def to_representation(self, instance):
        UserProfile.objects.get_or_create(user=instance)
        return super().to_representation(instance)


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(
        min_length=3,
        max_length=150,
        validators=(UnicodeUsernameValidator(),),
    )
    email = serializers.EmailField(max_length=254)
    first_name = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )
    last_name = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_username(self, value: str) -> str:
        normalized = value.strip()

        if User.objects.filter(username__iexact=normalized).exists():
            raise serializers.ValidationError(
                "A user with this username already exists."
            )

        return normalized

    def validate_email(self, value: str) -> str:
        normalized = value.strip().lower()

        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )

        return normalized

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "The passwords do not match."}
            )

        candidate = User(
            username=attrs["username"],
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )

        try:
            password_validation.validate_password(
                attrs["password"],
                user=candidate,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"password": list(exc.messages)}
            ) from exc

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            **validated_data,
        )
        UserProfile.objects.create(user=user)
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=254)
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    remember_me = serializers.BooleanField(
        required=False,
        default=False,
    )

    def validate(self, attrs):
        identifier = attrs["username"].strip()
        username = identifier

        email_user = User.objects.filter(
            email__iexact=identifier
        ).only("username").first()

        if email_user is not None:
            username = email_user.get_username()

        request = self.context.get("request")
        django_request = getattr(request, "_request", request)
        user = authenticate(
            request=django_request,
            username=username,
            password=attrs["password"],
        )

        if user is None or not user.is_active:
            raise serializers.ValidationError(
                "Invalid username, email address, or password."
            )

        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )
    new_password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user

        if not user.check_password(value):
            raise serializers.ValidationError(
                "The current password is incorrect."
            )

        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "The passwords do not match."}
            )

        user = self.context["request"].user

        try:
            password_validation.validate_password(
                attrs["new_password"],
                user=user,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages)}
            ) from exc

        return attrs
