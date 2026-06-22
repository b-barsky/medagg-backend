from datetime import datetime
from typing import List, Optional

class DatasetReadmeGenerator:
    """Generator of README.md files for datasets."""

    @staticmethod
    def format_size(size_bytes: Optional[int]) -> str:
        """Format size in bytes to readable format.

        Args:
            size_bytes: Size in bytes or None

        Returns:
            Formatted string like "1.23 MB" or "N/A"
        """
        if not size_bytes:
            return "N/A"

        # Handle negative or zero size
        if size_bytes <= 0:
            return "0 B"

        units = ['B', 'KB', 'MB', 'GB', 'TB']
        size = float(size_bytes)

        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.2f} {unit}"
            size /= 1024.0

        return f"{size:.2f} {units[-1]}"

    @staticmethod
    def format_list(items: List, empty_message: str = "Не указаны") -> str:
        """Format list of items for README.

        Args:
            items: List of items (should have __str__ method)
            empty_message: Message to show if list is empty

        Returns:
            Formatted string with each item on new line starting with "- "
        """
        if not items:
            return f"- {empty_message}"

        return "\n".join(f"- {item}" for item in items)

    @staticmethod
    def format_metadata_field(value, default: str = "Не указано") -> str:
        """Format metadata field value.

        Args:
            value: Field value
            default: Default value if field is empty

        Returns:
            Formatted string or default
        """
        if value is None or value == "":
            return default
        return str(value)

    @staticmethod
    def generate(dataset) -> str:
        """Generate README.md content for a dataset.

        Args:
            dataset: Dataset model instance (already loaded with relationships)

        Returns:
            README.md content as string
        """
        # Collect related data (optimized for already loaded relations)
        modalities = list(dataset.modalities.all())
        ml_tasks = list(dataset.ml_tasks.all())
        tags = list(dataset.tags.all())

        # Format size
        size_formatted = DatasetReadmeGenerator.format_size(dataset.size)

        # Format anatomical area
        anatomical_area = DatasetReadmeGenerator.format_metadata_field(
            dataset.anatomical_area.name if dataset.anatomical_area else None,
            default="Не указана"
        )

        # Format record count
        record_count = DatasetReadmeGenerator.format_metadata_field(
            dataset.record_count,
            default="N/A"
        )

        # Format dates safely
        created_at = dataset.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(dataset.created_at, 'strftime') else "N/A"
        updated_at = dataset.updated_at.strftime('%Y-%m-%d %H:%M') if hasattr(dataset.updated_at, 'strftime') else "N/A"

        # Build README content
        readme_lines = [
            f"# {dataset.title}\n",
            "## Описание",
            f"{dataset.description or 'Описание отсутствует'}\n",
            "## Метаданные",
            f"- **Анатомическая область**: {anatomical_area}",
            f"- **Количество записей**: {record_count}",
            f"- **Размер данных**: {size_formatted}",
            f"- **Внешний источник**: {DatasetReadmeGenerator.format_metadata_field(dataset.external_path)}",
            f"- **Локальный путь**: {DatasetReadmeGenerator.format_metadata_field(dataset.local_path)}",
            f"- **Дата создания**: {created_at}",
            f"- **Последнее обновление**: {updated_at}\n",
            "## Модальности",
            DatasetReadmeGenerator.format_list([m.name for m in modalities]) + "\n",
            "## Машинное обучение",
            "**Поддерживаемые задачи**:",
            DatasetReadmeGenerator.format_list([t.name for t in ml_tasks]) + "\n",
            "## Теги",
            DatasetReadmeGenerator.format_list([f"`{t.name}`" for t in tags], "Теги отсутствуют") + "\n",
            "## Структура данных",
            "*Информация о структуре данных будет добавлена после анализа датасета.*\n",
            "## Использование",
            "```python",
            "# Пример загрузки датасета",
            "import pandas as pd",
            "",
            "# Загрузка из локального пути",
            f"if '{dataset.local_path or ''}':",
            f"    df = pd.read_csv('{dataset.local_path or 'path/to/dataset.csv'}')",
            "    ",
            "# Или загрузка из внешнего источника",
            f"elif '{dataset.external_path or ''}':",
            f"    df = pd.read_csv('{dataset.external_path or 'https://example.com/dataset.csv'}')",
            "```\n",
            "## Контакты",
            "По вопросам использования датасета обращайтесь к администрации платформы.\n",
            "---",
            f"*README сгенерирован автоматически {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
        ]

        return "\n".join(readme_lines)

    @staticmethod
    def update_if_needed(dataset) -> bool:
        """Update README if it's empty or dataset was significantly updated.

        Args:
            dataset: Dataset model instance

        Returns:
            True if README was updated, False otherwise
        """
        if not dataset.readme_content:
            dataset.readme_content = DatasetReadmeGenerator.generate(dataset)
            return True

        # Optional: Add logic to check if dataset was significantly updated
        # For now, just update if README is empty
        return False

    @staticmethod
    def generate_from_dict(data: dict) -> str:
        """Generate README from dictionary data (for testing or API use).

        Args:
            data: Dictionary with dataset fields

        Returns:
            README.md content as string
        """
        # This method doesn't require model imports
        title = data.get('title', 'Untitled Dataset')
        description = data.get('description', 'Описание отсутствует')
        anatomical_area = data.get('anatomical_area', {}).get('name') if isinstance(data.get('anatomical_area'),
                                                                                    dict) else data.get(
            'anatomical_area')
        record_count = data.get('record_count')
        size = data.get('size')
        external_path = data.get('external_path')
        local_path = data.get('local_path')
        created_at = data.get('created_at', datetime.now())
        updated_at = data.get('updated_at', datetime.now())

        # Format size
        size_formatted = DatasetReadmeGenerator.format_size(size)

        # Format anatomical area
        anatomical_area_formatted = DatasetReadmeGenerator.format_metadata_field(
            anatomical_area,
            default="Не указана"
        )

        # Format record count
        record_count_formatted = DatasetReadmeGenerator.format_metadata_field(
            record_count,
            default="N/A"
        )

        # Format lists
        modalities = data.get('modalities', [])
        ml_tasks = data.get('ml_tasks', [])
        tags = data.get('tags', [])

        # Format dates
        if isinstance(created_at, str):
            created_at_formatted = created_at
        elif hasattr(created_at, 'strftime'):
            created_at_formatted = created_at.strftime('%Y-%m-%d %H:%M')
        else:
            created_at_formatted = "N/A"

        if isinstance(updated_at, str):
            updated_at_formatted = updated_at
        elif hasattr(updated_at, 'strftime'):
            updated_at_formatted = updated_at.strftime('%Y-%m-%d %H:%M')
        else:
            updated_at_formatted = "N/A"

        # Build README content
        readme_lines = [
            f"# {title}\n",
            "## Описание",
            f"{description}\n",
            "## Метаданные",
            f"- **Анатомическая область**: {anatomical_area_formatted}",
            f"- **Количество записей**: {record_count_formatted}",
            f"- **Размер данных**: {size_formatted}",
            f"- **Внешний источник**: {DatasetReadmeGenerator.format_metadata_field(external_path)}",
            f"- **Локальный путь**: {DatasetReadmeGenerator.format_metadata_field(local_path)}",
            f"- **Дата создания**: {created_at_formatted}",
            f"- **Последнее обновление**: {updated_at_formatted}\n",
            "## Модальности",
            DatasetReadmeGenerator.format_list([m.get('name', str(m)) for m in modalities]) + "\n",
            "## Машинное обучение",
            "**Поддерживаемые задачи**:",
            DatasetReadmeGenerator.format_list([t.get('name', str(t)) for t in ml_tasks]) + "\n",
            "## Теги",
            DatasetReadmeGenerator.format_list([f"`{t.get('name', str(t))}`" for t in tags], "Теги отсутствуют") + "\n",
            "## Структура данных",
            "*Информация о структуре данных будет добавлена после анализа датасета.*\n",
            "## Использование",
            "```python",
            "# Пример загрузки датасета",
            "import pandas as pd",
            "",
            "# Загрузка из локального пути",
            f"if '{local_path or ''}':",
            f"    df = pd.read_csv('{local_path or 'path/to/dataset.csv'}')",
            "    ",
            "# Или загрузка из внешнего источника",
            f"elif '{external_path or ''}':",
            f"    df = pd.read_csv('{external_path or 'https://example.com/dataset.csv'}')",
            "```\n",
            "## Контакты",
            "По вопросам использования датасета обращайтесь к администрации платформы.\n",
            "---",
            f"*README сгенерирован автоматически {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
        ]

        return "\n".join(readme_lines)