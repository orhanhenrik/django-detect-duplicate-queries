import traceback
from contextlib import contextmanager
from types import FrameType
from typing import Callable

from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.template import Node


def yellow(text):
    return f"\033[33m{text}\033[0m"


class DetectDuplicateQueriesMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with detect_duplicate_queries():
            return self.get_response(request)


class DetectDuplicateQueries:
    def __init__(self) -> None:
        self.stack_summaries: list[tuple[traceback.StackSummary, str]] = []
        self.stacks: list[list[tuple[FrameType, int]]] = []
        self.duplicates: dict[int, int] = {}

        # If this is True, items in the stacktrace that are from third party packages will be skipped.
        # A dot will be printed to represent an omitted stacktrace entry.
        self.compress_stacktrace = True

    def __call__(self, execute, sql, params, many, context):

        stack = list(reversed(list(traceback.walk_stack(None))))

        result = execute(sql, params, many, context)

        # StackSummary is used for comparison (have we seen this stack before?) and for pretty-printing the stacktrace.
        stack_summary = traceback.StackSummary.extract(stack)

        # If the same SQL came from the same stack trace previously, it is considered as a duplicate.
        if (stack_summary, sql) in self.stack_summaries:
            index = self.stack_summaries.index((stack_summary, sql))
            # self.duplicates needs to be indexed with a number because StackSummary is not hashable.
            self.duplicates[index] = self.duplicates.get(index, 1) + 1
        else:
            self.stack_summaries.append((stack_summary, sql))
            self.stacks.append(stack)

        return result

    @property
    def has_duplicates(self) -> bool:
        return bool(self.duplicates)

    def print_duplicates(self) -> None:
        if not self.duplicates:
            return

        print(yellow("\nDuplicate queries detected!"))

        for stack_index, count in self.duplicates.items():
            stack_summary, sql = self.stack_summaries[stack_index]
            stack_raw = self.stacks[stack_index]

            gap = False
            for formatted, (frame, _lineno) in zip(stack_summary.format(), stack_raw):

                filename = frame.f_code.co_filename
                is_package = "site-packages" in filename
                f_locals = frame.f_locals
                is_template_node = "self" in f_locals and isinstance(
                    f_locals["self"], Node
                )

                if self.compress_stacktrace and is_package and not is_template_node:
                    if not gap:
                        print("  ", end="")
                    print(".", end="")
                    gap = True
                    continue

                if gap:
                    print()

                if is_template_node:

                    node = f_locals["self"]

                    # There is usually multiple stack frames that process the same template line. For this rendering,
                    # we just want to show the template stack, so we can ignore any frames that have an identical Node
                    # as their predecessor.
                    if frame.f_back:
                        parent_locals = frame.f_back.f_locals
                        parent_is_template = "self" in parent_locals and isinstance(
                            parent_locals["self"], Node
                        )
                        if parent_is_template:
                            parent_node = parent_locals["self"]
                            if parent_node == node:
                                continue

                    print(f'  File "{node.origin.name}", line {node.token.lineno}')
                    print(f"    {node.token.contents}")
                else:
                    # formatted = ""
                    print(formatted, end="")

                gap = False

            print(yellow(f"\n^^ The above query was executed {count} times ^^\n"))

        print(
            f"Total of {len(self.duplicates)} duplicate queries ({sum(self.duplicates.values())} executions)"
        )


@contextmanager
def detect_duplicate_queries(*args, crash=False, **kwargs):
    duplicate_detector = DetectDuplicateQueries()

    with connection.execute_wrapper(duplicate_detector):
        yield duplicate_detector

    duplicate_detector.print_duplicates()

    if crash and duplicate_detector.duplicates:
        raise Exception("Duplicate queries detected. Crashing.")
