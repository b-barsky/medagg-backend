from rest_framework import serializers

from apps.datasets.models import *


class AnatomicalAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnatomicalArea
        fields = "__all__"


class ModalitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Modality
        fields = "__all__"


class MLTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = MLTask
        fields = "__all__"


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = "__all__"


class DatasetDetailedSerializer(serializers.ModelSerializer):
    anatomical_area_name = serializers.CharField(
        source="anatomical_area.name", read_only=True
    )
    modalities = ModalitySerializer(many=True, read_only=True)
    ml_tasks = MLTaskSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    readme_content = serializers.CharField(read_only=True)

    class Meta:
        model = Dataset
        fields = [
            "id",
            "title",
            "description",
            "external_path",
            "local_path",
            "record_count",
            "size",
            "license",
            "anatomical_area",
            "anatomical_area_name",
            "modalities",
            "ml_tasks",
            "tags",
            "created_at",
            "updated_at",
            "readme_content",
        ]
