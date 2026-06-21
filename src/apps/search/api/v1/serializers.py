from rest_framework import serializers


ORDERING_CHOICES = (
    "created_at",
    "-created_at",
    "title",
    "-title",
    "record_count",
    "-record_count",
    "size",
    "-size",
)


class CommaSeparatedListField(serializers.ListField):
    """
    Accepts either:

    ?tags_list=cancer,smoking

    or a normal Python/JSON list when used outside query parameters.
    """

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = [
                item.strip()
                for item in data.split(",")
                if item.strip()
            ]

        return super().to_internal_value(data)


class SearchDatasetsGetSerializer(serializers.Serializer):
    anatomical_area_name = serializers.CharField(
        required=False,
        allow_blank=False,
        min_length=2,
    )

    record_count_min = serializers.IntegerField(
        required=False,
        min_value=0,
    )
    record_count_max = serializers.IntegerField(
        required=False,
        min_value=0,
    )

    modalities_list = CommaSeparatedListField(
        child=serializers.CharField(min_length=1),
        required=False,
        allow_empty=False,
    )
    ml_tasks_list = CommaSeparatedListField(
        child=serializers.CharField(min_length=1),
        required=False,
        allow_empty=False,
    )
    tags_list = CommaSeparatedListField(
        child=serializers.CharField(min_length=1),
        required=False,
        allow_empty=False,
    )

    size_min = serializers.IntegerField(
        required=False,
        min_value=0,
    )
    size_max = serializers.IntegerField(
        required=False,
        min_value=0,
    )

    ordering = serializers.ChoiceField(
        choices=ORDERING_CHOICES,
        default="-created_at",
    )

    def validate(self, attrs):
        errors = {}

        range_fields = (
            ("record_count_min", "record_count_max"),
            ("size_min", "size_max"),
        )

        for minimum_field, maximum_field in range_fields:
            minimum = attrs.get(minimum_field)
            maximum = attrs.get(maximum_field)

            if (
                minimum is not None
                and maximum is not None
                and minimum > maximum
            ):
                errors[maximum_field] = (
                    f"Must be greater than or equal to {minimum_field}."
                )

        if errors:
            raise serializers.ValidationError(errors)

        return attrs


class SearchDatasetsPostSerializer(serializers.Serializer):
    query = serializers.CharField(
        max_length=100,
        min_length=2,
        allow_blank=False,
        trim_whitespace=True,
    )


class SearchDatasetsRequestSerializer(serializers.Serializer):
    get = SearchDatasetsGetSerializer()
    post = SearchDatasetsPostSerializer()