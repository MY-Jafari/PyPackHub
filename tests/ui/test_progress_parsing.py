"""Reading the progress a package manager reports out of its own output.

Two layers, both pure:

* the line parser picks a percentage out of one output line - pip's download
  redraws - and refuses the lookalikes (URL-encoded wheel names, values over
  one hundred, ordinary message lines);
* the run tracker feeds those lines, plus the work events pip and uv print
  (packages resolved, files fetched, the install phase, success), into one
  monotonic 0-100 value: the bar's fill.

Every number in the tests below is computed from the model, not chosen, so a
change in the spans or the counting shows up as a diff with a reason.
"""

from __future__ import annotations

from pypackhub.ui.formatting import parse_progress_percent
from pypackhub.ui.progress import RunProgressTracker


def test_a_rich_style_download_line_reports_its_percentage() -> None:
    assert parse_progress_percent("  \u2578\u2501\u2501\u2501  45%  2.3/5.1 MB") == 45


def test_an_old_style_bracket_bar_reports_its_percentage() -> None:
    assert parse_progress_percent("[====>     ]  45%") == 45


def test_a_space_before_the_percent_sign_still_counts() -> None:
    assert parse_progress_percent("  45 % done") == 45


def test_an_ordinary_line_reports_nothing() -> None:
    """Message lines must not move the bar; only progress lines do."""
    assert parse_progress_percent("Successfully installed requests-2.32.3") is None
    assert parse_progress_percent("Collecting requests==2.32.3") is None


def test_url_encoding_in_a_wheel_name_is_not_progress() -> None:
    """``pkg-2.0%2B1`` percent-encodes a local version, not a percentage.

    A naive ``NN%`` match would read the "0%" inside "2.0%2B1" and drag the
    bar to zero mid-download; the parser must not."""
    assert parse_progress_percent("Downloading pkg-2.0%2B1-py3-none-any.whl (64 kB)") is None


def test_carriage_return_redraws_report_the_newest_percentage() -> None:
    """A manager redrawing in place inside one chunk leaves several numbers;
    the last is the newest, and that is the one the bar must show."""
    line = "  12%  1.2/5.1 MB\r  33%  1.7/5.1 MB\r  51%  2.6/5.1 MB"
    assert parse_progress_percent(line) == 51


def test_a_number_over_one_hundred_is_not_a_percentage() -> None:
    assert parse_progress_percent("reused 150% of the cache") is None


def test_one_hundred_is() -> None:
    assert parse_progress_percent("[==========] 100%") == 100


# -- the run tracker -------------------------------------------------------------
#
# The bar's fill comes from the work events a manager reports, in the order
# it reports them; every number below is computed, not chosen.


def test_a_pip_install_fills_the_bar_event_by_event() -> None:
    """One package, fetch percentages and all: the bar tracks the file."""
    tracker = RunProgressTracker()

    assert tracker.observe("Collecting requests==2.32.3") is None  # nothing done yet
    assert tracker.observe("  Downloading requests-2.32.3-py3-none-any.whl (64 kB)") is None
    assert tracker.observe("     \u2501\u2501\u2501  12%  0.6/5.1 MB") == 9
    assert tracker.observe("     \u2501\u2501\u2501  68%  3.5/5.1 MB") == 54
    assert tracker.observe("     \u2501\u2501\u2501  100%  5.1/5.1 MB") == 79
    assert tracker.observe("Installing collected packages: requests") == 80
    assert tracker.observe("Successfully installed requests-2.32.3") == 100


def test_pip_without_percent_lines_still_counts_files() -> None:
    """Piped pip prints one line per fetch and no percentages at all - the
    files themselves are the progress, and each closes the one before it."""
    tracker = RunProgressTracker()
    for name in ("a", "b", "c"):
        tracker.observe(f"Collecting {name}")

    assert tracker.observe("Downloading a.whl (1 kB)") is None
    assert tracker.observe("Downloading b.whl (2 kB)") == 26  # a done: 80 * 1/3
    assert tracker.observe("Downloading c.whl (3 kB)") == 53  # b done: 80 * 2/3
    assert tracker.observe("Installing collected packages: c b a") == 80
    assert tracker.observe("Successfully installed a-1.0 b-1.0 c-1.0") == 100


def test_uv_flow_uses_the_count_it_announces() -> None:
    """uv says exactly how many packages it resolved - the fill is exact."""
    tracker = RunProgressTracker()

    assert tracker.observe("Resolved 3 packages in 150ms") == 10
    assert tracker.observe("Downloading a (64.3 KiB)") is None
    assert tracker.observe("Downloading b (121.8 KiB)") == 26
    assert tracker.observe("Downloading c (14.5 KiB)") == 53
    assert tracker.observe("Downloaded 3 packages in 300ms") == 80
    assert tracker.observe("Installed 3 packages in 100ms") == 95


def test_cached_files_are_processed_the_moment_they_are_named() -> None:
    tracker = RunProgressTracker()
    tracker.observe("Collecting requests")
    tracker.observe("Collecting urllib3")
    tracker.observe("Downloading requests-2.32.3-py3-none-any.whl (64 kB)")

    # The fetch in flight is done (pip works one file at a time) and the
    # cached one needed no fetching at all: both files are in place.
    assert tracker.observe("Using cached urllib3-2.2.2-py3-none-any.whl (121 kB)") == 79
    assert tracker.observe("Installing collected packages: urllib3 requests") == 80


def test_lines_that_report_nothing_move_nothing() -> None:
    tracker = RunProgressTracker()
    for line in ("Requirement already satisfied: requests", "hello there", ""):
        assert tracker.observe(line) is None
    assert tracker.value == 0


def test_a_percentage_line_outside_a_fetch_is_not_progress() -> None:
    """File percentages are only meaningful while a file is being fetched."""
    tracker = RunProgressTracker()
    assert tracker.observe("Satisfied 100% of the requirements") is None
    assert tracker.value == 0


def test_a_backward_redraw_never_lowers_the_bar() -> None:
    tracker = RunProgressTracker()
    tracker.observe("Collecting x")
    tracker.observe("Downloading x.whl (1 MB)")
    assert tracker.observe("  45%  0.4/1 MB") == 36
    assert tracker.observe("   3%  0.03/1 MB") is None  # high-water holds
    assert tracker.value == 36


def test_the_bar_never_claims_success_before_the_manager_announces_it() -> None:
    tracker = RunProgressTracker()
    tracker.observe("Collecting requests")
    tracker.observe("Downloading requests.whl (64 kB)")
    tracker.observe("Installing collected packages: requests")
    assert tracker.value == 80  # one phase short: success is not said yet
