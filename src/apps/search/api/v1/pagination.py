from django.conf import settings
from rest_framework.pagination import PageNumberPagination


class SearchResultPagination(PageNumberPagination):
    page_size = settings.SEARCH_RESULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = settings.SEARCH_RESULT_MAX_PAGE_SIZE

    def payload(self, data: list[dict]) -> dict[str, object]:
        return {
            "count": self.page.paginator.count,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "items": data,
        }
