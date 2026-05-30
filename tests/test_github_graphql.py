"""Tests for GitHub GraphQL helper."""

from unittest.mock import patch, MagicMock

from app.services.github import graphql_query


class TestGraphqlQuery:
    @patch("app.services.github.requests.post")
    def test_returns_data_on_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"repository": {"name": "test"}}}
        mock_post.return_value = mock_response

        result = graphql_query("query { repository { name } }", {})
        assert result == {"repository": {"name": "test"}}

    @patch("app.services.github.requests.post")
    def test_raises_on_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
        mock_post.return_value = mock_response

        try:
            graphql_query("query { }", {})
            assert False, "Should have raised"
        except Exception as e:
            assert "401" in str(e)

    @patch("app.services.github.requests.post")
    def test_raises_on_graphql_errors(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": [{"message": "Not found"}]}
        mock_post.return_value = mock_response

        try:
            graphql_query("query { }", {})
            assert False, "Should have raised"
        except Exception as e:
            assert "Not found" in str(e)
