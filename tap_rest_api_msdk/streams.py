"""Stream type classes for tap-rest-api-msdk."""

import email.utils
import json
from datetime import datetime
from string import Template
from typing import Any, Dict, Generator, Iterable, Optional, Union
from urllib.parse import parse_qs, parse_qsl, urlparse

import requests
from singer_sdk.helpers import types
from singer_sdk.helpers.jsonpath import extract_jsonpath
from singer_sdk.pagination import (
    BaseHATEOASPaginator,
    HeaderLinkPaginator,
    JSONPathPaginator,
    SimpleHeaderPaginator,
    SinglePagePaginator,
)
from tap_rest_api_msdk.client import RestApiStream
from tap_rest_api_msdk.pagination import (
    RestAPIBasePageNumberPaginator,
    RestAPIHeaderLinkPaginator,
    RestAPIOffsetPaginator,
    SimpleOffsetPaginator,
)
from tap_rest_api_msdk.utils import flatten_json, get_start_date

# Remove commented section to show http_request for debugging
# import logging
# import http.client

# http.client.HTTPConnection.debuglevel = 1

# logging.basicConfig()
# logging.getLogger().setLevel(logging.DEBUG)
# requests_log = logging.getLogger("requests.packages.urllib3")
# requests_log.setLevel(logging.DEBUG)
# requests_log.propagate = True


class DynamicStream(RestApiStream):
    """Define custom stream."""

    def __init__(
        self,
        tap: Any,
        name: str,
        records_path: str,
        path: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        primary_keys: Optional[list] = None,
        replication_key: Optional[str] = None,
        except_keys: Optional[list] = None,
        next_page_token_path: Optional[str] = None,
        schema: Optional[dict] = None,
        pagination_request_style: str = "default",
        pagination_response_style: str = "default",
        pagination_page_size: Optional[int] = None,
        pagination_results_limit: Optional[int] = None,
        pagination_next_page_param: Optional[str] = None,
        pagination_limit_per_page_param: Optional[str] = None,
        pagination_total_limit_param: Optional[str] = None,
        pagination_initial_offset: int = 1,
        offset_records_jsonpath: Optional[str] = None,
        start_date: Optional[datetime] = None,
        source_search_field: Optional[str] = None,
        source_search_query: Optional[str] = None,
        use_request_body_not_params: Optional[bool] = False,
        backoff_type: Optional[str] = None,
        backoff_param: Optional[str] = "Retry-After",
        backoff_time_extension: Optional[int] = 0,
        store_raw_json_message: Optional[bool] = False,
        authenticator: Optional[object] = None,
    ) -> None:
        """Class initialization.

        Args:
            tap: see tap.py
            name: see tap.py
            path: see tap.py
            params: see tap.py
            headers: see tap.py
            primary_keys: see tap.py
            replication_key: see tap.py
            except_keys: see tap.py
            records_path: see tap.py
            next_page_token_path: see tap.py
            schema: the json schema for the stream.
            pagination_request_style: see tap.py
            pagination_response_style: see tap.py
            pagination_page_size: see tap.py
            pagination_results_limit: see tap.py
            pagination_next_page_param: see tap.py
            pagination_limit_per_page_param: see tap.py
            pagination_total_limit_param: see tap.py
            pagination_initial_offset: see tap.py
            start_date: see tap.py
            source_search_field: see tap.py
            source_search_query: see tap.py
            use_request_body_not_params: see tap.py
            backoff_type: see tap.py
            backoff_param: see tap.py
            backoff_time_extension: see tap.py
            store_raw_json_message: see tap.py
            authenticator: see tap.py

        """
        super().__init__(tap=tap, name=tap.name, schema=schema)

        if primary_keys is None:
            primary_keys = []

        self.name = name
        self.path = path
        self.params = params if params else {}
        self.headers = headers
        self.assigned_authenticator = authenticator
        self._authenticator = authenticator
        self.primary_keys = primary_keys
        self.replication_key = replication_key
        self.except_keys = except_keys
        self.records_path = records_path

        if next_page_token_path:
            self.next_page_token_jsonpath = next_page_token_path
        elif (
            pagination_request_style == "jsonpath_paginator"
            or pagination_request_style == "default"
        ):
            self.next_page_token_jsonpath = (
                "$.next_page"  # Set default for jsonpath_paginator
            )
        get_url_params_styles = {
            "style1": self._get_url_params_offset_style,
            "offset": self._get_url_params_offset_style,
            "page": self._get_url_params_page_style,
            "header_link": self._get_url_params_header_link,
            "hateoas_body": self._get_url_params_hateoas_body,
        }

        # Selecting the appropriate method to send Parameters as part of the
        # request. If use_request_body_not_params is set the parameters are sent
        # in the request body instead of request parameters. The
        # pagination_response_style config determines what style of parameter
        # processing is invoked.

        self.use_request_body_not_params = use_request_body_not_params
        self.backoff_type = backoff_type
        self.backoff_param = backoff_param
        self.backoff_time_extension = backoff_time_extension
        self.store_raw_json_message = store_raw_json_message
        if self.use_request_body_not_params:
            self.prepare_request_payload = get_url_params_styles.get(  # type: ignore
                pagination_response_style, self._get_url_params_page_style
            )  # Defaults to page_style url_params
        else:
            self.get_url_params = get_url_params_styles.get(  # type: ignore
                pagination_response_style, self._get_url_params_page_style
            )  # Defaults to page_style url_params

        self.pagination_request_style = pagination_request_style
        self.pagination_results_limit = pagination_results_limit
        self.pagination_next_page_param = pagination_next_page_param
        self.pagination_limit_per_page_param = pagination_limit_per_page_param
        self.pagination_total_limit_param = pagination_total_limit_param
        self.start_date = start_date
        self.source_search_field = source_search_field
        self.source_search_query = source_search_query
        self.pagination_page_size: Optional[int]
        self.pagination_initial_offset = pagination_initial_offset
        self.offset_records_jsonpath = offset_records_jsonpath

        # Setting Pagination Limits
        if self.pagination_request_style == "restapi_header_link_paginator":
            if pagination_page_size:
                self.pagination_page_size = pagination_page_size
            else:
                if self.pagination_limit_per_page_param:
                    page_limit_param = self.pagination_limit_per_page_param
                else:
                    page_limit_param = "per_page"
                self.pagination_page_size = int(
                    self.params.get(page_limit_param, 25)
                )  # Default to requesting 25 records
        elif (
            self.pagination_request_style == "style1"
            or self.pagination_request_style == "offset_paginator"
        ):
            if self.pagination_results_limit:
                self.ABORT_AT_RECORD_COUNT = (
                    self.pagination_results_limit
                )  # Will raise an exception.
            if pagination_page_size:
                self.pagination_page_size = pagination_page_size
            else:
                if self.pagination_limit_per_page_param:
                    page_limit_param = self.pagination_limit_per_page_param
                else:
                    page_limit_param = "limit"
                self.pagination_page_size = int(
                    self.params.get(page_limit_param, 25)
                )  # Default to requesting 25 records
        else:
            if self.pagination_results_limit:
                self.ABORT_AT_RECORD_COUNT = (
                    self.pagination_results_limit
                )  # Will raise an exception.
            self.pagination_page_size = pagination_page_size

        # GitHub is missing the "since" parameter on a few endpoints
        # set this parameter to True if your stream needs to navigate data in
        # descending order
        # and try to exit early on its own.
        # This only has effect on streams whose `replication_key` is `updated_at`.
        self.use_fake_since_parameter = False

    @property
    def http_headers(self) -> dict:
        """Return the http headers needed.

        Returns:
              A dictionary of the headers to be included in the request.

        """
        headers = {}
        if "user_agent" in self.config:
            headers["User-Agent"] = self.config.get("user_agent")
        # If not using an authenticator, you may also provide inline auth headers:
        # headers["Private-Token"] = self.config.get("auth_token")

        if self.headers:
            for k, v in self.headers.items():
                headers[k] = v

        return headers

    def backoff_wait_generator(
        self,
    ) -> Generator[Union[int, float], None, None]:
        """Return a backoff generator as required to manage Rate Limited APIs.

        Supply a backoff_type in the config to indicate the style of backoff.
        If the backoff response is in a header, supply a backoff_param
        indicating what key contains the backoff delay.

        Note: If the backoff_type is message, the message is parsed for numeric
        values. It is assumed that the highest numeric value discovered is the
        backoff value in seconds.

        Returns:
            Backoff Generator with value to wait based on the API Response.

        """

        def _backoff_from_headers(exception):
            response_headers = exception.response.headers

            return (
                int(response_headers.get(self.backoff_param, 0))
                + self.backoff_time_extension
            )

        def _get_wait_time_from_response(exception):
            response_message = exception.response.json().get("message", 0)
            res = [int(i) for i in response_message.split() if i.isdigit()]

            return int(max(res)) + self.backoff_time_extension

        if self.backoff_type == "message":
            return self.backoff_runtime(value=_get_wait_time_from_response)
        elif self.backoff_type == "header":
            return self.backoff_runtime(value=_backoff_from_headers)
        else:
            # No override required. Use SDK backoff_wait_generator
            return super().backoff_wait_generator()

    def get_new_paginator(self):
        """Return the requested paginator required to retrieve all data from the API.

        Returns:
            Paginator Class.

        """
        self.logger.info(
            f"the next_page_token_jsonpath = {self.next_page_token_jsonpath}."
        )

        if (
            self.pagination_request_style == "jsonpath_paginator"
            or self.pagination_request_style == "default"
        ):
            return JSONPathPaginator(self.next_page_token_jsonpath)
        elif (
            self.pagination_request_style == "simple_header_paginator"
        ):  # Example Gitlab.com
            if self.next_page_token_jsonpath:
                return JSONPathPaginator(self.next_page_token_jsonpath)

            return SimpleHeaderPaginator("X-Next-Page")
        elif self.pagination_request_style == "header_link_paginator":
            return HeaderLinkPaginator()
        elif (
            self.pagination_request_style == "restapi_header_link_paginator"
        ):  # Example GitHub.com
            return RestAPIHeaderLinkPaginator(
                pagination_page_size=self.pagination_page_size,
                pagination_results_limit=self.pagination_results_limit,
                replication_key=self.replication_key,
            )
        elif (
            self.pagination_request_style == "style1"
            or self.pagination_request_style == "offset_paginator"
        ):
            return RestAPIOffsetPaginator(
                start_value=self.pagination_initial_offset,
                page_size=self.pagination_page_size,
                jsonpath=self.next_page_token_jsonpath,
                pagination_total_limit_param=self.pagination_total_limit_param,
            )
        elif self.pagination_request_style == "hateoas_paginator":
            return BaseHATEOASPaginator()
        elif self.pagination_request_style == "single_page_paginator":
            return SinglePagePaginator()
        elif self.pagination_request_style == "page_number_paginator":
            return RestAPIBasePageNumberPaginator(
                jsonpath=self.next_page_token_jsonpath
            )
        elif self.pagination_request_style == "simple_offset_paginator":
            return SimpleOffsetPaginator(
                start_value=self.pagination_initial_offset,
                page_size=self.pagination_page_size,
                offset_records_jsonpath=self.offset_records_jsonpath,
                pagination_page_size=self.pagination_page_size,
            )
        else:
            self.logger.error(
                f"Unknown paginator {self.pagination_request_style}. Please declare "
                f"a valid paginator."
            )
            raise ValueError(
                f"Unknown paginator {self.pagination_request_style}. Please declare "
                f"a valid paginator."
            )

    def _get_url_params_page_style(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: optional - the singer context object.
            next_page_token: optional - the token for the next page of results.

        Returns:
            An object containing the parameters to add to the request.

        """
        # Initialise Starting Values
        last_run_date = get_start_date(self, context)
        params: dict = {}
        if self.params:
            for k, v in self.params.items():
                params[k] = v
        if next_page_token:
            if self.pagination_next_page_param:
                next_page_parm = self.pagination_next_page_param
            else:
                next_page_parm = "page"
            params[next_page_parm] = next_page_token
        if self.replication_key:
            # Use incremental replication (if available) via a filter query being
            # sent to the API This assumes storing a replication timestamp and querying
            # records greater than that date in subsequent runs. Config the appropriate
            # source field and query template.
            if self.source_search_field and self.source_search_query and last_run_date:
                query_template = Template(self.source_search_query)
                if self.use_request_body_not_params:
                    params[self.source_search_field] = json.loads(
                        query_template.substitute(last_run_date=last_run_date)
                    )
                else:
                    params[self.source_search_field] = query_template.substitute(
                        last_run_date=last_run_date
                    )
            else:
                params["sort"] = "asc"
                params["order_by"] = self.replication_key

        return params

    def _get_url_params_offset_style(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: optional - the singer context object.
            next_page_token: optional - the token for the next page of results.

        Returns:
            An object containing the parameters to add to the request.

        """
        # Initialise Starting Values
        last_run_date = get_start_date(self, context)
        params: dict = {}

        if self.params:
            for k, v in self.params.items():
                params[k] = v
        if next_page_token:
            if self.pagination_next_page_param:
                next_page_parm = self.pagination_next_page_param
            else:
                next_page_parm = "offset"
            params[next_page_parm] = next_page_token
        if self.pagination_page_size is not None:
            if self.pagination_limit_per_page_param:
                limit_per_page_param = self.pagination_limit_per_page_param
            else:
                limit_per_page_param = "limit"
            params[limit_per_page_param] = self.pagination_page_size
        if self.replication_key:
            # Use incremental replication (if available) via a filter query being sent
            # to the API This assumes storing a replication timestamp and querying
            # records greater than that date in subsequent runs. Config the appropriate
            # source field and query template.
            if self.source_search_field and self.source_search_query and last_run_date:
                query_template = Template(self.source_search_query)
                if self.use_request_body_not_params:
                    params[self.source_search_field] = json.loads(
                        query_template.substitute(last_run_date=last_run_date)
                    )
                else:
                    params[self.source_search_field] = query_template.substitute(
                        last_run_date=last_run_date
                    )
            else:
                params["sort"] = "asc"
                params["order_by"] = self.replication_key

        return params

    def _get_url_params_header_link(
        self, context: Optional[Dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Logic based on https://github.com/MeltanoLabs/tap-github

        Args:
            context: optional - the singer context object.
            next_page_token: optional - the token for the next page of results.

        Returns:
            An object containing the parameters to add to the request.

        """
        params: dict = {}
        if self.params:
            for k, v in self.params.items():
                params[k] = v
        if self.pagination_page_size:
            pagination_page_size = self.pagination_page_size
        else:
            pagination_page_size = 25  # Default to 25 per page if not set
        if self.pagination_limit_per_page_param:
            limit_per_page_param = self.pagination_limit_per_page_param
        else:
            limit_per_page_param = "per_page"
        params[limit_per_page_param] = pagination_page_size
        if next_page_token:
            request_parameters = parse_qs(str(next_page_token))
            for k, v in request_parameters.items():
                params[k] = v

        if self.replication_key == "updated_at":
            params["sort"] = "updated"
            params["direction"] = "desc" if self.use_fake_since_parameter else "asc"

        # Unfortunately the /starred, /stargazers (starred_at) and /events (created_at)
        # endpoints do not support the "since" parameter out of the box. But we use a
        # workaround in 'get_next_page_token'.
        elif self.replication_key in ["starred_at", "created_at"]:
            params["sort"] = "created"
            params["direction"] = "desc"

        # Warning: /commits endpoint accept "since" but results are ordered by
        # descending commit_timestamp
        elif self.replication_key == "commit_timestamp":
            params["direction"] = "desc"

        elif self.replication_key:
            self.logger.warning(
                f"The replication key '{self.replication_key}' is not fully supported "
                f"by this client yet."
            )

        since = self.get_starting_timestamp(context)
        since_key = "since" if not self.use_fake_since_parameter else "fake_since"
        if self.replication_key and since:
            params[since_key] = since
            # Leverage conditional requests to save API quotas
            # https://github.community/t/how-does-if-modified-since-work/139627
            self._http_headers["If-modified-since"] = email.utils.format_datetime(since)

        return params

    def _get_url_params_hateoas_body(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: optional - the singer context object.
            next_page_token: optional - the token for the next page of results.


            HATEOAS stands for "Hypermedia as the Engine of Application State".
             See https://en.wikipedia.org/wiki/HATEOAS.

            Note: Under the HATEOAS model, the returned token contains all the
            required parameters for the subsequent call. The function splits the
            parameters into Dict key value pairs for subsequent requests.

        Returns:
            An object containing the parameters to add to the request.

        """
        # Initialise Starting Values
        last_run_date = get_start_date(self, context)
        params: dict = {}

        if self.params:
            for k, v in self.params.items():
                params[k] = v

        # Set Pagination Limits if required.
        if self.pagination_page_size and self.pagination_limit_per_page_param:
            params[self.pagination_limit_per_page_param] = self.pagination_page_size

        if next_page_token:
            # Parse the next_page_token for the path and parameters
            url_parsed = urlparse(next_page_token)
            if url_parsed.query:
                params.update(parse_qsl(url_parsed.query))
            else:
                params.update(parse_qsl(url_parsed.path))
            if url_parsed.path == next_page_token:
                self.path = ""
            else:
                self.path = url_parsed.path
        elif self.replication_key:
            # Use incremental replication (if available) via a filter query being sent
            # to the API This assumes storing a replication timestamp and querying
            # records greater than that date in subsequent runs. Config the appropriate
            # source field and query template.
            if self.source_search_field and self.source_search_query and last_run_date:
                query_template = Template(self.source_search_query)
                if self.use_request_body_not_params:
                    params[self.source_search_field] = json.loads(
                        query_template.substitute(last_run_date=last_run_date)
                    )
                else:
                    params[self.source_search_field] = query_template.substitute(
                        last_run_date=last_run_date
                    )
            elif self.source_search_field and last_run_date:
                params[self.source_search_field] = "gt" + last_run_date

        return params

    def parse_response(self, response: requests.Response) -> Iterable[dict]:
        """Parse the response and return an iterator of result rows.

        Args:
            response: required - the requests.Response given by the api call.

        Yields:
              Parsed records.
        """
        try:
            json_response = response.json()
            # Extract records using the configured records_path
            records = extract_jsonpath(self.records_path, input=json_response)
            
            # Store the raw response context if needed for post-processing
            self._last_response_context = json_response
            
            yield from records
        except Exception as e:
            self.logger.error(f"Error parsing response: {str(e)}")
            self.logger.error(f"Response content: {response.text}")
            raise

    def post_process(
        self,
        row: types.Record,
        context: Optional[types.Context] = None,
    ) -> Optional[dict]:
        """Process each record to extract and transform data as needed.

        Args:
            row: required - the record for processing.
            context: optional - the singer context object.

        Returns:
              A processed record with consistent schema.
        """
        try:
            # Get the extraction paths from stream config
            extraction_paths = self.config.get("extraction_paths", {})
            
            if extraction_paths:
                # Extract specific fields based on configured paths
                extracted_data = {}
                for field_name, jsonpath in extraction_paths.items():
                    values = list(extract_jsonpath(jsonpath, input=row))
                    if values:
                        extracted_data[field_name] = values[0] if len(values) == 1 else values
                
                # Add any additional context from the response if needed
                if hasattr(self, '_last_response_context'):
                    for field_name, jsonpath in self.config.get("response_context_paths", {}).items():
                        values = list(extract_jsonpath(jsonpath, input=self._last_response_context))
                        if values:
                            extracted_data[field_name] = values[0] if len(values) == 1 else values
                
                processed_row = extracted_data
            else:
                # If no extraction paths specified, flatten the entire row
                processed_row = flatten_json(row, self.except_keys, self.store_raw_json_message)
            
            # Add parameter context to identify which parameter combination generated this record
            if hasattr(self, '_current_params'):
                processed_row['_parameters'] = self._current_params

            return processed_row
        except Exception as e:
            self.logger.error(f"Error post-processing record: {str(e)}")
            self.logger.error(f"Original record: {row}")
            raise

    def get_records(self, context: Optional[dict]) -> Iterable[dict]:
        """Return a generator of row-type dictionary objects.

        Args:
            context: The stream context.

        Yields:
            Each record from the source.
        """
        # If any param value is a list, we need to iterate through all combinations
        param_lists = {}
        for k, v in self.params.items():
            if isinstance(v, list):
                param_lists[k] = v

        if param_lists:
            # Get all combinations of parameter values
            from itertools import product
            param_names = list(param_lists.keys())
            param_values = [param_lists[k] for k in param_names]
            
            # Iterate through all combinations
            for values in product(*param_values):
                # Create parameter combination
                current_params = self.params.copy()
                param_context = {}
                for name, value in zip(param_names, values):
                    current_params[name] = value
                    param_context[name] = value
                
                # Store current parameter context for post_process
                self._current_params = param_context
                
                # Temporarily set the params
                original_params = self.params
                self.params = current_params
                
                try:
                    # Get records with current parameter combination
                    for record in super().get_records(context):
                        yield record
                finally:
                    # Restore original params
                    self.params = original_params
                    self._current_params = None
        else:
            # If no list parameters, proceed normally
            for record in super().get_records(context):
                yield record
