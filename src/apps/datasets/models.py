from django.db import models
from django.utils import timezone

class AnatomicalArea(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Modality(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MLTask(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Dataset(models.Model):
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, null=True)
    external_path = models.CharField(max_length=1000, blank=True, null=True)
    local_path = models.CharField(max_length=500, blank=True, null=True)
    record_count = models.IntegerField(blank=True, null=True)
    size = models.IntegerField(blank=True, null=True)
    license = models.CharField(max_length=500, blank=True)
    anatomical_area = models.ForeignKey(
        AnatomicalArea, on_delete=models.SET_NULL, null=True
    )
    modalities = models.ManyToManyField(Modality, through="DatasetModality")
    ml_tasks = models.ManyToManyField(MLTask, through="DatasetMLTask")
    tags = models.ManyToManyField(Tag, through="DatasetTag")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    readme_content = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Override save to update timestamps and generate README."""
        self.updated_at = timezone.now()

        # Call parent save first to get ID for relationships
        super().save(*args, **kwargs)

        # Generate README if empty - ЛЕНИВЫЙ ИМПОРТ
        if not self.readme_content:
            from .services.readme_generator import DatasetReadmeGenerator
            self.readme_content = DatasetReadmeGenerator.generate(self)
            # Save only readme_content field to avoid recursion
            super().save(update_fields=['readme_content'])

    def update_readme(self):
        """Force update of README content."""
        # ЛЕНИВЫЙ ИМПОРТ для избежания циклической зависимости
        from .services.readme_generator import DatasetReadmeGenerator
        self.readme_content = DatasetReadmeGenerator.generate(self)
        self.save(update_fields=['readme_content', 'updated_at'])

    def get_readme_generator(self):
        """Get the README generator instance."""
        # ЛЕНИВЫЙ ИМПОРТ
        from .services.readme_generator import DatasetReadmeGenerator
        return DatasetReadmeGenerator

    @property
    def formatted_size(self):
        """Get formatted size string."""
        # ЛЕНИВЫЙ ИМПОРТ
        from .services.readme_generator import DatasetReadmeGenerator
        return DatasetReadmeGenerator.format_size(self.size)

    @property
    def formatted_anatomical_area(self):
        """Get formatted anatomical area string."""
        # ЛЕНИВЫЙ ИМПОРТ
        from .services.readme_generator import DatasetReadmeGenerator
        area_name = self.anatomical_area.name if self.anatomical_area else None
        return DatasetReadmeGenerator.format_metadata_field(
            area_name, default="Не указана"
        )

    def generate_readme(self):
        """Generate README content."""
        # ЛЕНИВЫЙ ИМПОРТ
        from .services.readme_generator import DatasetReadmeGenerator
        return DatasetReadmeGenerator.generate(self)


class DatasetModality(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    modality = models.ForeignKey(Modality, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("dataset", "modality")


class DatasetMLTask(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    ml_task = models.ForeignKey(MLTask, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("dataset", "ml_task")


class DatasetTag(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("dataset", "tag")