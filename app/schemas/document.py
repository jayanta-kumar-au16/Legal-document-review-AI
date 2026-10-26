from pydantic import BaseModel


class DocumentUploadData(BaseModel):
    document_id: str
    file_name: str
    extraction_status: str
    file_type: str
    # None for DOCX: Word files have no fixed pages.
    page_count: int | None
    section_count: int
    character_count: int
    warnings: list[str] = []


class PageTextData(BaseModel):
    page: int | None
    text: str


class SectionRef(BaseModel):
    label: str
    title: str
    page: int | None


class DocumentTextData(BaseModel):
    document_id: str
    file_name: str
    pages: list[PageTextData]
    sections: list[SectionRef]


class ClauseSource(BaseModel):
    page: int | None
    section: str | None


class ClauseData(BaseModel):
    clause_type: str
    status: str  # always "found": only clauses that exist in the document are listed
    evidence: str  # the clause text, as it appears in the document
    summary: str | None  # only filled by method=llm
    source: ClauseSource


class DocumentClausesData(BaseModel):
    document_id: str
    file_name: str
    file_type: str | None
    method: str
    clause_count: int
    clauses: list[ClauseData]
    warnings: list[str]


class PlaybookSummary(BaseModel):
    playbook_id: str
    version: str
    name: str
    rule_count: int
    built_in: bool
    is_default: bool = False


class PlaybookListData(BaseModel):
    default_playbook_id: str
    playbooks: list[PlaybookSummary]
