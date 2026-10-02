# Phase 2 browser discovery evidence

Observed 2026-10-02 using the connected Chrome session, without reading credentials,
cookies, storage, request headers or network responses.

The existing session reached the authenticated course list through the visible
My courses navigation. All seven PRD course names were present on the first page.
The course list has pagination; first-page results alone do not establish a
complete catalog for future semesters.

For Operating Systems, navigation through Chapters to Courseware showed five
activities (lecture/lab entries). Each displayed a title, a View file label, and a
chapter/unit. The default ordering label was chapter/unit, ascending.

No PDF filename, upload timestamp, or content hash was visible in this list.
Therefore activity entries cannot yet be converted into verified PdfMaterial
records or advertised as newest-upload ordering. Activity detail inspection and
file identity discovery remain required. Do not synthesize upload timestamps from
chapter order, observation time or course dates.

No activity was submitted, no assignment was changed, and no form was filled.
This observation verifies browser access and courseware navigation only, not the
implementation of an automatic material connector.

## Activity detail follow-up

The previously tested Lecture 0 activity exposes a PDF filename, a displayed size
of 455.37 KB, and a reference-file download link. The activity identifier and
reference-file identifier are distinct, so the activity itself must not be used
as the unique identity of every attachment it might contain.

The activity time field is unset. No upload timestamp was visible; activity time
would not establish upload time even if present. Material metadata therefore
permits an unknown upload date and sorts such records last without claiming a
complete newest-first ordering. Discovering genuine upload timestamps remains an
unfulfilled PRD requirement.

The UI describes viewing/downloading reference documents as an activity completion
criterion. Future browser retrieval must account for the platform's possible
automatic view/progress tracking; a read operation cannot promise zero server-side
analytics effects. No completion control, submission or edit action was invoked.
