"""Generate alteryx/parliament_pipeline.yxmd

The workflow is written as code so every tool setting is reviewable in git.
Open the generated .yxmd in Alteryx Designer to run or edit it; if you edit
it in Designer, re-export the change here (or stop using the generator).

    python alteryx/build_workflow.py

Layout (tool containers, left to right inside each):
  1  Extract     config table -> $count -> page list -> Download -> JSON Parse
                 -> Cross Tab (one row per record, all three API entities)
  2  Business    type + clean -> dedupe -> DQ rules -> topics (split + join CSV)
  3  People      members + authors -> join (orphan check)
  4  Quality     rejects + row-count reconciliation vs the API's own $count
  5  Load        Snowflake landing tables; Post-SQL runs the MERGE procedures
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).resolve().parent
OUT = HERE / "parliament_pipeline.yxmd"

# Snowflake connection. A Windows ODBC DSN (Snowflake driver, key-pair auth)
# named "snowflake_parliament" pointing at database PARLIAMENT. See docs/SETUP.md.
SNOWFLAKE = "odbc:DSN=snowflake_parliament"
SINCE_DATE = "2025-01-01"
BASE_URL = "https://ws.parlament.ch/odata.svc"

BASE = ("AlteryxBasePluginsGui", "AlteryxBasePluginsEngine.dll")
PLUGINS = {
    "TextInput":     (f"{BASE[0]}.TextInput.TextInput", BASE[1], "AlteryxTextInput"),
    "Formula":       (f"{BASE[0]}.Formula.Formula", BASE[1], "AlteryxFormula"),
    "Filter":        (f"{BASE[0]}.Filter.Filter", BASE[1], "AlteryxFilter"),
    "Select":        (f"{BASE[0]}.AlteryxSelect.AlteryxSelect", BASE[1], "AlteryxSelect"),
    # Download lives in the connector DLLs, not the base plugins (checked against Designer 2026.3 samples)
    "Download":      ("AlteryxConnectorGui.Download.Download", "AlteryxConnectorEngine.dll", "AlteryxDownload"),
    "JSONParse":     (f"{BASE[0]}.JSONParse.JSONParse", BASE[1], "AlteryxJSONParse"),
    "GenerateRows":  (f"{BASE[0]}.GenerateRows.GenerateRows", BASE[1], "AlteryxGenerateRows"),
    "CrossTab":      (f"{BASE[0]}.CrossTab.CrossTab", BASE[1], "AlteryxCrossTab"),
    "Unique":        (f"{BASE[0]}.Unique.Unique", BASE[1], "AlteryxUnique"),
    "Join":          (f"{BASE[0]}.Join.Join", BASE[1], "AlteryxJoin"),
    "Union":         (f"{BASE[0]}.Union.Union", BASE[1], "AlteryxUnion"),
    "TextToColumns": (f"{BASE[0]}.TextToColumns.TextToColumns", BASE[1], "AlteryxTextToColumns"),
    "Input":         (f"{BASE[0]}.DbFileInput.DbFileInput", BASE[1], "AlteryxDbFileInput"),
    "Output":        (f"{BASE[0]}.DbFileOutput.DbFileOutput", BASE[1], "AlteryxDbFileOutput"),
    "Summarize":     ("AlteryxSpatialPluginsGui.Summarize.Summarize", "AlteryxSpatialPluginsEngine.dll", "AlteryxSummarize"),
}

MAXW = 1073741823  # Alteryx max V_WString size
TEXT_CAP = 16000   # long texts (body, reason); the same cap is in tools/reference_check.py


def a(v) -> str:
    """XML attribute value (quoted + escaped)."""
    return quoteattr(str(v))


def t(v) -> str:
    """XML text node."""
    return escape(str(v))


class Workflow:
    def __init__(self) -> None:
        self.next_id = 1
        self.nodes: dict[int, str] = {}
        self.parent: dict[int, int | None] = {}
        self.containers: list[int] = []
        self.conns: list[str] = []

    # ------------------------------------------------------------------ core
    def _id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    def tool(self, kind: str, x: int, y: int, config: str, note: str = "", box: int | None = None) -> int:
        gui, dll, entry = PLUGINS[kind]
        i = self._id()
        self.nodes[i] = f"""
    <Node ToolID="{i}">
      <GuiSettings Plugin="{gui}">
        <Position x="{x}" y="{y}" />
      </GuiSettings>
      <Properties>
        <Configuration>{config}
        </Configuration>
        <Annotation DisplayMode="{0 if note else 2}">
          <Name />
          <AnnotationText>{t(note)}</AnnotationText>
          <DefaultAnnotationText />
          <Left value="False" />
        </Annotation>
      </Properties>
      <EngineSettings EngineDll="{dll}" EngineDllEntryPoint="{entry}" />
    </Node>"""
        self.parent[i] = box
        return i

    def container(self, caption: str, x: int, y: int, w: int, h: int, fill: str) -> int:
        i = self._id()
        self.nodes[i] = f"""
    <Node ToolID="{i}">
      <GuiSettings Plugin="AlteryxGuiToolkit.ToolContainer.ToolContainer">
        <Position x="{x}" y="{y}" width="{w}" height="{h}" />
      </GuiSettings>
      <Properties>
        <Configuration>
          <Caption>{t(caption)}</Caption>
          <Style TextColor="#314c4a" FillColor="{fill}" BorderColor="#314c4a" Transparency="25" Margin="25" />
          <Disabled value="False" />
          <Folded value="False" />
        </Configuration>
        <Annotation DisplayMode="0">
          <Name />
          <DefaultAnnotationText />
          <Left value="False" />
        </Annotation>
      </Properties>
      <ChildNodes>%CHILDREN_{i}%
      </ChildNodes>
    </Node>"""
        self.parent[i] = None
        self.containers.append(i)
        return i

    def comment(self, text: str, x: int, y: int, w: int, h: int, box: int | None = None) -> int:
        i = self._id()
        self.nodes[i] = f"""
    <Node ToolID="{i}">
      <GuiSettings Plugin="AlteryxGuiToolkit.TextBox.TextBox">
        <Position x="{x}" y="{y}" width="{w}" height="{h}" />
      </GuiSettings>
      <Properties>
        <Configuration>
          <Text>{t(text)}</Text>
          <Font name="Segoe UI" size="9" style="0" />
          <TextColor name="Black" />
          <FillColor r="255" g="255" b="240" />
          <Shape shape="0" />
          <Justification Justification="0" />
        </Configuration>
        <Annotation DisplayMode="0">
          <Name />
          <DefaultAnnotationText />
          <Left value="False" />
        </Annotation>
      </Properties>
    </Node>"""
        self.parent[i] = box
        return i

    def connect(self, src: int, dst: int, out: str = "Output", inp: str = "Input", name: str | None = None) -> None:
        n = f" name={a(name)}" if name else ""
        self.conns.append(f"""
    <Connection{n}>
      <Origin ToolID="{src}" Connection="{out}" />
      <Destination ToolID="{dst}" Connection="{inp}" />
    </Connection>""")

    # ------------------------------------------------------- tool helpers
    def text_input(self, x, y, fields: list[str], rows: list[list[str]], note="", box=None):
        f = "".join(f'\n            <Field name={a(n)} />' for n in fields)
        r = "".join("\n            <r>" + "".join(f"<c>{t(c)}</c>" for c in row) + "</r>" for row in rows)
        cfg = f"""
          <NumRows value="{len(rows)}" />
          <Fields>{f}
          </Fields>
          <Data>{r}
          </Data>"""
        return self.tool("TextInput", x, y, cfg, note, box)

    def formula(self, x, y, fields: list[tuple], note="", box=None):
        """fields: (name, type, size, expression)"""
        ff = "".join(
            f"\n            <FormulaField expression={a(expr)} field={a(name)} size={a(size)} type={a(typ)} />"
            for name, typ, size, expr in fields)
        return self.tool("Formula", x, y, f"\n          <FormulaFields>{ff}\n          </FormulaFields>", note, box)

    def filter(self, x, y, expr: str, note="", box=None):
        cfg = f"\n          <Expression>{t(expr)}</Expression>\n          <Mode>Custom</Mode>"
        return self.tool("Filter", x, y, cfg, note, box)

    def select(self, x, y, keep: list, note="", box=None):
        """keep: field names, or (field, rename, type, size) tuples. Everything else is dropped."""
        rows = []
        for k in keep:
            if isinstance(k, str):
                rows.append(f'\n            <SelectField field={a(k)} selected="True" />')
            else:
                field, rename, typ, size = k
                extra = ""
                if rename:
                    extra += f" rename={a(rename)}"
                if typ:
                    extra += f" type={a(typ)} size={a(size)}"
                rows.append(f'\n            <SelectField field={a(field)} selected="True"{extra} />')
        rows.append('\n            <SelectField field="*Unknown" selected="False" />')
        cfg = f"""
          <OrderChanged value="True" />
          <CommaDecimal value="False" />
          <SelectFields>{''.join(rows)}
          </SelectFields>"""
        return self.tool("Select", x, y, cfg, note, box)

    def download(self, x, y, url_field: str, note="", box=None):
        # No Accept header on purpose: the API's firewall rejects "Accept: application/json"
        # on the $count call (it answers text/plain). JSON pages use $format=json instead.
        # Layout follows the Download tool in Designer's own sample "05 Download_and_parse_web_data".
        # *Unknown selected="False" in Headers/ComposeFields: no other column is sent as a header
        # or query parameter. URLs are encoded by the Formula tool, so EncodeURLs is off.
        cfg = f"""
          <URLField>{t(url_field)}</URLField>
          <OutputMode>String</OutputMode>
          <CodePage>65001</CodePage>
          <EncodeURLs value="False" />
          <Headers>
            <NameValues>
              <Item name="User-Agent" value="parliament-data-pipeline (portfolio project)" />
            </NameValues>
            <Fields orderChanged="False">
              <Field name="*Unknown" selected="False" />
            </Fields>
          </Headers>
          <Payload>
            <HTTPAction>GET</HTTPAction>
            <QueryStringBodyMode>Compose</QueryStringBodyMode>
            <ComposeNameValues />
            <ComposeFields orderChanged="False">
              <Field name="*Unknown" selected="False" />
            </ComposeFields>
          </Payload>
          <UserName />
          <Password />
          <numConnections>2</numConnections>
          <Timeout>600</Timeout>"""
        return self.tool("Download", x, y, cfg, note, box)

    def json_parse(self, x, y, field: str, note="", box=None):
        cfg = f"""
          <JSON_Field>{t(field)}</JSON_Field>
          <IncludeInOutput value="False" />
          <SingleField value="True" />"""
        return self.tool("JSONParse", x, y, cfg, note, box)

    def generate_rows(self, x, y, name, init, cond, loop, note="", box=None):
        cfg = f"""
          <UpdateField value="False" />
          <UpdateField_Name />
          <CreateField_Name>{t(name)}</CreateField_Name>
          <CreateField_Type>Int32</CreateField_Type>
          <CreateField_Size>4</CreateField_Size>
          <Expression_Init>{t(init)}</Expression_Init>
          <Expression_Cond>{t(cond)}</Expression_Cond>
          <Expression_Loop>{t(loop)}</Expression_Loop>"""
        return self.tool("GenerateRows", x, y, cfg, note, box)

    def cross_tab(self, x, y, group: list[str], header: str, data: str, method="First", note="", box=None):
        g = "".join(f"\n            <Field field={a(f)} />" for f in group)
        cfg = f"""
          <GroupFields orderChanged="False">{g}
          </GroupFields>
          <HeaderField field={a(header)} />
          <DataField field={a(data)} />
          <Methods>
            <Method method="{method}" />
            <Separator>,</Separator>
            <FieldSize value="{MAXW}" />
          </Methods>"""
        return self.tool("CrossTab", x, y, cfg, note, box)

    def unique(self, x, y, fields: list[str], note="", box=None):
        f = "".join(f"\n            <Field field={a(n)} />" for n in fields)
        return self.tool("Unique", x, y, f"\n          <UniqueFields>{f}\n          </UniqueFields>", note, box)

    def join(self, x, y, left: list[str], right: list[str], note="", box=None):
        lf = "".join(f"\n            <Field field={a(n)} />" for n in left)
        rf = "".join(f"\n            <Field field={a(n)} />" for n in right)
        cfg = f"""
          <JoinInfo connection="Left">{lf}
          </JoinInfo>
          <JoinInfo connection="Right">{rf}
          </JoinInfo>
          <SelectConfiguration>
            <Configuration outputConnection="Join">
              <OrderChanged value="False" />
              <CommaDecimal value="False" />
              <SelectFields>
                <SelectField field="*Unknown" selected="True" />
              </SelectFields>
            </Configuration>
          </SelectConfiguration>"""
        node = self.tool("Join", x, y, cfg, note, box)
        self.nodes[node] = self.nodes[node].replace("<Configuration>", '<Configuration joinByRecordPos="False">', 1)
        return node

    def union(self, x, y, note="", box=None, missing_fields="Warning"):
        cfg = f"""
          <ByName_ErrorMode>{missing_fields}</ByName_ErrorMode>
          <ByName_OutputMode>All</ByName_OutputMode>
          <Mode>ByName</Mode>
          <SetOutputOrder value="False" />"""
        return self.tool("Union", x, y, cfg, note, box)

    def split_rows(self, x, y, field: str, delimiter: str, note="", box=None):
        cfg = f"""
          <Field>{t(field)}</Field>
          <ErrorHandling>Last</ErrorHandling>
          <RootName>{t(field)}</RootName>
          <Delimeters value={a(delimiter)} />
          <Flags value="0" />
          <NumFields value="1" />
          <SplitToRows value="True" />"""
        return self.tool("TextToColumns", x, y, cfg, note, box)

    def summarize(self, x, y, fields: list[tuple], note="", box=None):
        """fields: (field, action, rename)"""
        f = "".join(f"\n            <SummarizeField field={a(fl)} action={a(ac)} rename={a(rn)} />"
                    for fl, ac, rn in fields)
        return self.tool("Summarize", x, y, f"\n          <SummarizeFields>{f}\n          </SummarizeFields>", note, box)

    def csv_input(self, x, y, path: str, note="", box=None):
        cfg = f"""
          <Passwords />
          <File OutputFileName="" RecordLimit="" SearchSubDirs="False" FileFormat="0">{t(path)}</File>
          <FormatSpecificOptions>
            <HeaderRow>True</HeaderRow>
            <IgnoreErrors>False</IgnoreErrors>
            <AllowShareWrite>False</AllowShareWrite>
            <ImportLine>1</ImportLine>
            <FieldLen>254</FieldLen>
            <SingleThreadRead>False</SingleThreadRead>
            <IgnoreQuotes>DoubleQuotes</IgnoreQuotes>
            <Delimeter>,</Delimeter>
            <QuoteRecordBreak>False</QuoteRecordBreak>
            <CodePage>65001</CodePage>
          </FormatSpecificOptions>"""
        return self.tool("Input", x, y, cfg, note, box)

    def yxdb_output(self, x, y, path: str, note="", box=None):
        cfg = f"""
          <File MaxRecords="" FileFormat="19">{t(path)}</File>
          <Passwords />
          <FormatSpecificOptions>
            <NoSpatialIndex>False</NoSpatialIndex>
            <SaveSource>True</SaveSource>
          </FormatSpecificOptions>
          <MultiFile value="False" />"""
        return self.tool("Output", x, y, cfg, note, box)

    def snowflake_output(self, x, y, table: str, mode: str, post_sql: str = "", note="", box=None):
        """mode: Delete = 'Delete Data & Append', Append = 'Append Existing'."""
        cfg = f"""
          <File MaxRecords="" FileFormat="23">{t(SNOWFLAKE + '|||' + table)}</File>
          <Passwords />
          <FormatSpecificOptions>
            <TransactionSize>10000</TransactionSize>
            <IgnoreDropTableSQLErrors>False</IgnoreDropTableSQLErrors>
            <SpatialObjSize>8000</SpatialObjSize>
            <OutputOption>{mode}</OutputOption>
            <PostSQL>{t(post_sql)}</PostSQL>
            <PreSQL />
            <TableStyle>Quoted</TableStyle>
          </FormatSpecificOptions>
          <MultiFile value="False" />"""
        return self.tool("Output", x, y, cfg, note, box)

    # ---------------------------------------------------------------- write
    def xml(self) -> str:
        top = []
        children: dict[int, list[str]] = {c: [] for c in self.containers}
        for i, body in self.nodes.items():
            p = self.parent.get(i)
            (children[p] if p else top).append((i, body))
        out = []
        for i, body in top:
            if i in children:
                body = body.replace(f"%CHILDREN_{i}%", "".join(
                    b.replace("\n", "\n    ") for _, b in children[i]))
            out.append(body)
        return f"""<?xml version="1.0"?>
<AlteryxDocument yxmdVer="2024.1">
  <Nodes>{''.join(out)}
  </Nodes>
  <Connections>{''.join(self.conns)}
  </Connections>
  <Properties>
    <Memory default="True" />
    <GlobalRecordLimit value="0" />
    <TempFiles default="True" />
    <Annotation on="True" includeToolName="False" />
    <ConvErrorLimit value="10" />
    <ConvErrorLimit_Stop value="False" />
    <CancelOnError value="False" />
    <DisableBrowse value="False" />
    <EnablePerformanceProfiling value="True" />
    <RunWithE2 value="True" />
    <DisableAllOutput value="False" />
    <ShowAllMacroMessages value="False" />
    <ShowConnectionStatusIsOn value="True" />
    <ShowConnectionStatusOnlyWhenRunning value="False" />
    <ZoomLevel value="0" />
    <LayoutType>Horizontal</LayoutType>
    <Constants>
      <Constant>
        <Namespace>User</Namespace>
        <Name>SinceDate</Name>
        <Value>{SINCE_DATE}</Value>
        <IsNumeric value="False" />
      </Constant>
    </Constants>
    <MetaInfo>
      <NameIsFileName value="True" />
      <Name>parliament_pipeline</Name>
      <Description>Swiss Parliament (Curia Vista OData API) -> clean + data quality -> Snowflake landing tables -> MERGE. Cortex AI enrichment runs in Snowflake on the changed rows.</Description>
      <RootToolName />
      <ToolVersion />
      <ToolInDb value="False" />
      <CategoryName />
      <SearchTags>snowflake, odata, api, json, data quality, cortex</SearchTags>
      <Author>Oskari Kaile</Author>
      <Company />
      <Copyright />
      <DescriptionLink actual="" displayed="" />
      <Example>
        <Description />
        <File />
      </Example>
    </MetaInfo>
    <Events>
      <Enabled value="True" />
    </Events>
  </Properties>
</AlteryxDocument>
"""


# ======================================================================
# Shared expressions
# ======================================================================
def clean_html(src: str) -> str:
    """Alteryx expression: strip HTML tags, decode the 4 entities the API uses, squeeze spaces, cap TEXT_CAP chars."""
    return (
        "Left(Trim(REGEX_Replace("
        "Replace(Replace(Replace(Replace("
        f"REGEX_Replace({src}, '<[^>]+>', ' '),"
        " '&nbsp;', ' '), '&lt;', '<'), '&gt;', '>'), '&amp;', '&'),"
        f" '\\s+', ' ')), {TEXT_CAP})"
    )


def epoch_ms(field: str, fmt: str) -> str:
    """OData '/Date(1741910400000)/' -> formatted date string (UTC)."""
    return (f"IIF(IsEmpty([{field}]), Null(), DateTimeFormat(DateTimeAdd('1970-01-01 00:00:00', "
            f"FLOOR(ToNumber(REGEX_Replace([{field}], '[^0-9]', '')) / 1000), 'seconds'), '{fmt}'))")


RUN_ID = ("RUN_ID", "V_String", 20, "DateTimeFormat(DateTimeStart(), '%Y%m%d%H%M%S')")


def reject(wf, x, y, entity: str, key_expr: str, reason: str, box) -> int:
    """Formula + Select that shape any rejected record into the OPS.DQ_REJECTS layout."""
    f = wf.formula(x, y, [
        RUN_ID,
        ("RECORD_KEY", "V_WString", 100, key_expr),   # before ENTITY: the key may read the old [ENTITY]
        ("REASON", "V_WString", 200, f"'{reason}'"),
        ("ENTITY", "V_String", 50, f"'{entity}'"),
    ], box=box)
    s = wf.select(x + 72, y, ["RUN_ID", "ENTITY", "RECORD_KEY", "REASON"], box=box)
    wf.connect(f, s)
    return f, s


# ======================================================================
# Build
# ======================================================================
wf = Workflow()

wf.comment(
    "Swiss Parliament pipeline (Alteryx -> Snowflake)\n"
    "Source: Curia Vista OData API (ws.parlament.ch), business items, authors and members since "
    "[User.SinceDate] (Workflow Configuration > Constants).\n"
    "Adding a new API entity = one more row in the config Text Input (tool 1 in container 1).\n"
    "Snowflake: ODBC DSN 'snowflake_parliament'. Each Output writes a RAW landing table, then its Post-SQL "
    "calls the MERGE procedure (CORE). Cortex AI enrichment is a Snowflake task on the CORE stream.",
    30, 10, 1380, 62)

# ---------------------------------------------------------------- 1 Extract
C1 = wf.container("1  Extract: config-driven OData API ingestion (count -> pages -> JSON -> records)",
                  30, 90, 1380, 210, "#e8f1f2")
cfg = wf.text_input(60, 170, ["ENTITY", "FILTER", "FIELDS", "BASE_URL", "PAGE_SIZE"], [
    ["Business",
     "Language eq 'DE' and SubmissionDate ge datetime'{SINCE}T00:00:00'",
     "ID,BusinessShortNumber,BusinessTypeAbbreviation,BusinessTypeName,Title,Description,SubmittedText,"
     "ReasonText,InitialSituation,BusinessStatusText,ResponsibleDepartmentAbbreviation,"
     "SubmissionCouncilAbbreviation,Tags,TagNames,SubmissionDate,Modified",
     BASE_URL, "1000"],
    ["BusinessRole",
     "Language eq 'DE' and Role eq 7 and BusinessSubmissionDate ge datetime'{SINCE}T00:00:00'",
     "ID,BusinessNumber,MemberCouncilNumber,CommitteeNumber,CantonNumber,ParlGroupNumber,Role",
     BASE_URL, "1000"],
    ["MemberCouncil",
     "Language eq 'DE' and (DateLeaving eq null or DateLeaving ge datetime'{SINCE}T00:00:00')",
     "ID,FirstName,LastName,PartyAbbreviation,ParlGroupAbbreviation,CantonAbbreviation,CouncilAbbreviation,Active",
     BASE_URL, "1000"],
], note="Config: one row per API entity", box=C1)

enc = "Replace(Replace(Replace([FILTER], '{SINCE}', [User.SinceDate]), ' ', '%20'), \"'\", '%27')"
f_count = wf.formula(150, 170, [
    ("FILTER_ENC", "V_WString", 1000, enc),
    ("PAGE_SIZE_N", "Int32", 4, "ToNumber([PAGE_SIZE])"),
    ("COUNT_URL", "V_WString", 2000, "[BASE_URL] + '/' + [ENTITY] + '/$count?$filter=' + [FILTER_ENC]"),
], note="Build $count URL", box=C1)
dl_count = wf.download(240, 170, "COUNT_URL", note="GET row count", box=C1)
f_pages = wf.formula(330, 170, [
    ("TOTAL", "Int64", 8, "ToNumber(Trim([DownloadData]))"),
    ("N_PAGES", "Int32", 4, "CEIL([TOTAL] / [PAGE_SIZE_N])"),
], note="Pages needed", box=C1)
s_pages = wf.select(420, 170, ["ENTITY", "FILTER_ENC", "FIELDS", "BASE_URL", "PAGE_SIZE_N", "TOTAL", "N_PAGES"],
                    box=C1)
gen = wf.generate_rows(510, 170, "PAGE", "0", "[PAGE] < [N_PAGES]", "[PAGE] + 1",
                       note="One row per page", box=C1)
f_url = wf.formula(600, 170, [
    ("PAGE_URL", "V_WString", 4000,
     "[BASE_URL] + '/' + [ENTITY] + '?$filter=' + [FILTER_ENC] + '&$select=' + [FIELDS] + "
     "'&$orderby=ID&$top=' + ToString([PAGE_SIZE_N]) + '&$skip=' + ToString([PAGE] * [PAGE_SIZE_N]) + "
     "'&$format=json'"),
], note="Page URL ($top/$skip)", box=C1)
dl_page = wf.download(690, 170, "PAGE_URL", note="GET page (JSON)", box=C1)
f_http = wf.formula(780, 170, [("HTTP_STATUS", "V_String", 3, "GetWord([DownloadHeaders], 1)")], box=C1)
flt_http = wf.filter(870, 170, "[HTTP_STATUS] = '200'", note="HTTP 200?", box=C1)
s_pre = wf.select(915, 120, ["ENTITY", "TOTAL", "PAGE", "PAGE_SIZE_N", "DownloadData"], box=C1)
jp = wf.json_parse(960, 140, "DownloadData", note="Flatten JSON", box=C1)
f_key = wf.formula(1050, 140, [
    ("REC", "V_String", 10, "REGEX_Replace([JSON_Name], '^d\\.(?:results\\.)?(\\d+)\\..*$', '$1')"),
    ("FIELD", "V_String", 100, "REGEX_Replace([JSON_Name], '^d\\.(?:results\\.)?\\d+\\.', '')"),
    ("REC_KEY", "Int64", 8, "[PAGE] * [PAGE_SIZE_N] + ToNumber([REC])"),
], note="Record index + field name", box=C1)
flt_top = wf.filter(1140, 140, "REGEX_Match([JSON_Name], '^d\\.(results\\.)?\\d+\\.[A-Za-z]+$')",
                    note="Drop __metadata", box=C1)
xt = wf.cross_tab(1230, 140, ["ENTITY", "TOTAL", "REC_KEY"], "FIELD", "JSON_ValueString",
                  note="Rows -> one record per row", box=C1)
snap = wf.yxdb_output(1320, 210, "..\\data\\api_snapshot.yxdb", note="Local raw snapshot", box=C1)

wf.connect(cfg, f_count)
wf.connect(f_count, dl_count)
wf.connect(dl_count, f_pages)
wf.connect(f_pages, s_pages)
wf.connect(s_pages, gen)
wf.connect(gen, f_url)
wf.connect(f_url, dl_page)
wf.connect(dl_page, f_http)
wf.connect(f_http, flt_http)
wf.connect(flt_http, s_pre, out="True")
wf.connect(s_pre, jp)
wf.connect(jp, f_key)
wf.connect(f_key, flt_top)
wf.connect(flt_top, xt, out="True")
wf.connect(xt, snap)

# ---------------------------------------------------------------- 2 Business
C2 = wf.container("2  Transform: business items + official topics", 30, 330, 1380, 250, "#eef3e6")
fb = wf.filter(60, 400, "[ENTITY] = 'Business'", note="Business rows", box=C2)
wf.connect(xt, fb)
fb_t = wf.formula(150, 400, [
    ("BUSINESS_ID", "Int64", 8, "ToNumber([ID])"),
    ("SHORT_NUMBER", "V_WString", 20, "[BusinessShortNumber]"),
    ("BUSINESS_TYPE", "V_WString", 20, "[BusinessTypeAbbreviation]"),
    ("BUSINESS_TYPE_NAME", "V_WString", 200, "[BusinessTypeName]"),
    # Sized to the data (6 Nov 2026: title max 323, body 8,570, reason 12,060 chars). The ODBC output
    # batches rows by width: 1 GB fields meant one row per INSERT (~1 h), 60k fields ~2 rows (~25 min).
    # clean_html() caps text at TEXT_CAP, so a longer text is cut explicitly, same as the Python twin.
    ("TITLE_DE", "V_WString", 1000, "Trim([Title])"),
    ("BODY_RAW", "V_WString", MAXW,
     "IIF(!IsEmpty([SubmittedText]), [SubmittedText], IIF(!IsEmpty([InitialSituation]), [InitialSituation], [Description]))"),
    ("BODY_DE", "V_WString", TEXT_CAP, clean_html("[BODY_RAW]")),
    ("REASON_DE", "V_WString", TEXT_CAP, clean_html("[ReasonText]")),
    ("STATUS", "V_WString", 200, "[BusinessStatusText]"),
    ("DEPARTMENT", "V_WString", 20, "[ResponsibleDepartmentAbbreviation]"),
    ("COUNCIL", "V_WString", 10, "[SubmissionCouncilAbbreviation]"),
    ("TAG_IDS", "V_WString", 200, "[Tags]"),
    ("TAG_NAMES", "V_WString", 1000, "[TagNames]"),
    ("TAG_COUNT", "Int16", 2, "IIF(IsEmpty([TagNames]), 0, REGEX_CountMatches([TagNames], '\\|') + 1)"),
    ("BODY_CHARS", "Int32", 4, "Length([BODY_DE])"),
    ("SUBMITTED_ON", "Date", 10, epoch_ms("SubmissionDate", "%Y-%m-%d")),
    ("MODIFIED_AT", "DateTime", 19, epoch_ms("Modified", "%Y-%m-%d %H:%M:%S")),
    ("SOURCE_URL", "V_WString", 300,
     "'https://www.parlament.ch/de/ratsbetrieb/suche-curia-vista/geschaeft?AffairId=' + [ID]"),
    RUN_ID,
    ("LOADED_AT", "DateTime", 19, "DateTimeStart()"),
], note="Types, HTML -> text, epoch -> date", box=C2)
BUS_COLS = ["BUSINESS_ID", "SHORT_NUMBER", "BUSINESS_TYPE", "BUSINESS_TYPE_NAME", "TITLE_DE", "BODY_DE",
            "REASON_DE", "STATUS", "DEPARTMENT", "COUNCIL", "TAG_IDS", "TAG_NAMES", "TAG_COUNT", "BODY_CHARS",
            "SUBMITTED_ON", "MODIFIED_AT", "SOURCE_URL", "RUN_ID", "LOADED_AT"]
fb_s = wf.select(240, 400, ["ENTITY", "TOTAL"] + BUS_COLS, box=C2)
fb_u = wf.unique(330, 400, ["BUSINESS_ID"], note="Dedupe on ID", box=C2)
fb_dq = wf.filter(420, 400,
                  "!IsNull([BUSINESS_ID]) AND !IsEmpty([TITLE_DE]) AND !IsNull([SUBMITTED_ON])",
                  note="DQ: id, title, date", box=C2)
fb_out = wf.select(510, 380, BUS_COLS, note="Landing layout", box=C2)
wf.connect(fb, fb_t, out="True")
wf.connect(fb_t, fb_s)
wf.connect(fb_s, fb_u)
wf.connect(fb_u, fb_dq, out="Unique")
wf.connect(fb_dq, fb_out, out="True")

# topics: split the pipe list to rows and join the reference CSV
tp_s = wf.select(510, 470, ["BUSINESS_ID", "TAG_NAMES", "RUN_ID"], box=C2)
tp_split = wf.split_rows(600, 470, "TAG_NAMES", "|", note="One row per topic", box=C2)
tp_nonempty = wf.filter(690, 470, "!IsEmpty(Trim([TAG_NAMES]))", box=C2)
ref = wf.csv_input(600, 540, "..\\reference\\topics.csv", note="Reference CSV (28 topics)", box=C2)
ref_s = wf.select(690, 540, [("TAG_ID", "", "Int32", 4), ("TOPIC_DE", "", "V_WString", 200),
                             ("TOPIC_FR", "", "V_WString", 200), ("TOPIC_EN", "", "V_WString", 200),
                             ("TOPIC_DESCRIPTION_EN", "", "V_WString", 500)], box=C2)
tp_join = wf.join(780, 490, ["TAG_NAMES"], ["TOPIC_DE"], note="Topic DE -> id + EN", box=C2)
tp_out = wf.select(870, 470, ["BUSINESS_ID", "TAG_ID", "TOPIC_DE", "TOPIC_EN", "RUN_ID"], box=C2)
wf.connect(fb_dq, tp_s, out="True")
wf.connect(tp_s, tp_split)
wf.connect(tp_split, tp_nonempty)
wf.connect(tp_nonempty, tp_join, out="True", inp="Left")
wf.connect(ref, ref_s)
wf.connect(ref_s, tp_join, inp="Right")
wf.connect(tp_join, tp_out, out="Join")

# ---------------------------------------------------------------- 3 People
C3 = wf.container("3  Transform: members + authors (who submitted what)", 30, 610, 1380, 230, "#f3eee6")
fm = wf.filter(60, 680, "[ENTITY] = 'MemberCouncil'", note="Member rows", box=C3)
wf.connect(xt, fm)
fm_t = wf.formula(150, 680, [
    ("MEMBER_ID", "Int64", 8, "ToNumber([ID])"),
    ("FIRST_NAME", "V_WString", 200, "[FirstName]"),
    ("LAST_NAME", "V_WString", 200, "[LastName]"),
    ("PARTY", "V_WString", 50, "[PartyAbbreviation]"),
    ("PARL_GROUP", "V_WString", 50, "[ParlGroupAbbreviation]"),
    ("CANTON", "V_WString", 5, "[CantonAbbreviation]"),
    ("COUNCIL", "V_WString", 10, "[CouncilAbbreviation]"),
    # Not "ACTIVE": Alteryx field names ignore case, so that would overwrite the API's [Active]
    # field and keep its spelling, and Snowflake would look for a column named "Active".
    # The API sends a JSON boolean; in single-string mode JSON Parse turns it into text, so accept
    # every spelling ('true', '1', '-1'). Run 4 compared only to 'true' and marked all 272 inactive.
    ("IS_ACTIVE", "Bool", 1, "Lowercase(Trim([Active])) IN ('true', '1', '-1', 'yes')"),
    RUN_ID,
], note="Typed member fields", box=C3)
MEM_COLS = ["MEMBER_ID", "FIRST_NAME", "LAST_NAME", "PARTY", "PARL_GROUP", "CANTON", "COUNCIL", "ACTIVE", "RUN_ID"]
fm_s = wf.select(240, 680, ["ENTITY", "TOTAL"] + [("IS_ACTIVE", "ACTIVE", "", "") if c == "ACTIVE" else c for c in MEM_COLS], box=C3)
fm_u = wf.unique(330, 680, ["MEMBER_ID"], note="Dedupe on ID", box=C3)
fm_dq = wf.filter(420, 680, "!IsNull([MEMBER_ID]) AND !IsEmpty([LAST_NAME])", note="DQ: id, name", box=C3)
fm_out = wf.select(510, 660, MEM_COLS, note="Landing layout", box=C3)
fm_slim = wf.select(510, 740, ["MEMBER_ID", "PARTY", "CANTON"], box=C3)
wf.connect(fm, fm_t, out="True")
wf.connect(fm_t, fm_s)
wf.connect(fm_s, fm_u)
wf.connect(fm_u, fm_dq, out="Unique")
wf.connect(fm_dq, fm_out, out="True")
wf.connect(fm_dq, fm_slim, out="True")

fr = wf.filter(60, 780, "[ENTITY] = 'BusinessRole'", note="Author rows", box=C3)
wf.connect(xt, fr)
fr_t = wf.formula(150, 780, [
    ("BUSINESS_ID", "Int64", 8, "ToNumber([BusinessNumber])"),
    ("MEMBER_ID", "Int64", 8, "IIF(IsEmpty([MemberCouncilNumber]), Null(), ToNumber([MemberCouncilNumber]))"),
    ("COMMITTEE_ID", "Int64", 8, "IIF(IsEmpty([CommitteeNumber]), Null(), ToNumber([CommitteeNumber]))"),
    ("AUTHOR_TYPE", "V_String", 20,
     "IIF(!IsNull([MEMBER_ID]), 'member', IIF(!IsEmpty([CommitteeNumber]), 'committee', "
     "IIF(!IsEmpty([CantonNumber]), 'canton', IIF(!IsEmpty([ParlGroupNumber]), 'parl_group', 'other'))))"),
    ("ROLE_ID", "V_String", 40, "[ID]"),
    RUN_ID,
], note="Author type", box=C3)
fr_s = wf.select(240, 780, ["ENTITY", "TOTAL", "ROLE_ID", "BUSINESS_ID", "AUTHOR_TYPE", "MEMBER_ID",
                            "COMMITTEE_ID", "RUN_ID"], box=C3)
fr_dq = wf.filter(330, 780, "!IsNull([BUSINESS_ID])", note="DQ: business id", box=C3)
fr_mem = wf.filter(420, 780, "[AUTHOR_TYPE] = 'member'", box=C3)
fr_join = wf.join(600, 780, ["MEMBER_ID"], ["MEMBER_ID"], note="Author -> party, canton", box=C3)
# Committee, canton and group authors have no party or canton by design, so a missing field
# on those inputs is expected, not a warning.
fr_union = wf.union(690, 780, box=C3, missing_fields="Ignore")
fr_out = wf.select(780, 780, ["BUSINESS_ID", "AUTHOR_TYPE", "MEMBER_ID", "COMMITTEE_ID", "PARTY", "CANTON",
                              "RUN_ID"], note="Landing layout", box=C3)
wf.connect(fr, fr_t, out="True")
wf.connect(fr_t, fr_s)
wf.connect(fr_s, fr_dq)
wf.connect(fr_dq, fr_mem, out="True")
wf.connect(fr_mem, fr_join, out="True", inp="Left")
wf.connect(fm_slim, fr_join, inp="Right")
wf.connect(fr_join, fr_union, out="Join", name="#1")
wf.connect(fr_join, fr_union, out="Left", name="#2")   # author not in member list: keep row, flag below
wf.connect(fr_mem, fr_union, out="False", name="#3")   # committees, cantons, groups
wf.connect(fr_union, fr_out)

# ---------------------------------------------------------------- 4 Quality
C4 = wf.container("4  Data quality: rejects + reconciliation against the API's own $count",
                  30, 870, 1380, 250, "#f6e9e9")
rejects = [
    reject(wf, 60, 940, "Business", "ToString([BUSINESS_ID])", "duplicate id", C4),
    reject(wf, 60, 990, "Business", "ToString([BUSINESS_ID])", "missing id, title or date", C4),
    reject(wf, 60, 1040, "BusinessTopic", "ToString([BUSINESS_ID]) + ':' + [TAG_NAMES]",
           "unknown topic (update reference/topics.csv)", C4),
    reject(wf, 240, 940, "MemberCouncil", "ToString([MEMBER_ID])", "duplicate id", C4),
    reject(wf, 240, 990, "MemberCouncil", "ToString([MEMBER_ID])", "missing id or name", C4),
    reject(wf, 240, 1040, "BusinessRole", "[ROLE_ID]", "missing business id", C4),
    reject(wf, 420, 940, "BusinessRole", "ToString([BUSINESS_ID]) + ':' + ToString([MEMBER_ID])",
           "author not in member list", C4),
    reject(wf, 420, 990, "API", "[ENTITY] + ' page ' + ToString([PAGE])", "HTTP status not 200", C4),
]
wf.connect(fb_u, rejects[0][0], out="Duplicates")
wf.connect(fb_dq, rejects[1][0], out="False")
wf.connect(tp_join, rejects[2][0], out="Left")
wf.connect(fm_u, rejects[3][0], out="Duplicates")
wf.connect(fm_dq, rejects[4][0], out="False")
wf.connect(fr_dq, rejects[5][0], out="False")
wf.connect(fr_join, rejects[6][0], out="Left")
wf.connect(flt_http, rejects[7][0], out="False")
rej_union = wf.union(600, 990, note="All rejects", box=C4)
for n, (_, s) in enumerate(rejects, 1):
    wf.connect(s, rej_union, name=f"#{n}")
rej_sum = wf.summarize(690, 1040, [("RUN_ID", "First", "RUN_ID"), ("ENTITY", "GroupBy", "ENTITY"),
                                   ("REASON", "GroupBy", "CHECK_NAME"), ("REASON", "Count", "FAILED_ROWS")],
                       note="Rejects per reason", box=C4)
rej_dq = wf.formula(780, 1040, [
    ("EXPECTED", "Int64", 8, "0"),
    ("ACTUAL", "Int64", 8, "[FAILED_ROWS]"),
    ("STATUS", "V_String", 10, "IIF(Contains([CHECK_NAME], 'unknown topic'), 'FAIL', 'WARN')"),
    ("DETAIL", "V_WString", 1000, "ToString([FAILED_ROWS]) + ' row(s) rejected, see OPS.DQ_REJECTS'"),
], box=C4)
wf.connect(rej_union, rej_sum)
wf.connect(rej_sum, rej_dq)

# Reconciliation = extract completeness: records received per entity (after the
# Cross Tab, before dedupe/DQ) must equal the $count the API reported. Bad source
# rows (duplicates, empty titles) are WARN rejects above, not a FAIL here.
recon_sum = wf.summarize(600, 1090, [("ENTITY", "GroupBy", "ENTITY"), ("TOTAL", "Max", "EXPECTED"),
                                     ("REC_KEY", "Count", "ACTUAL")], note="Received vs $count", box=C4)
wf.connect(xt, recon_sum)
recon_f = wf.formula(690, 1090, [
    RUN_ID,
    ("CHECK_NAME", "V_WString", 100, "'extract_complete'"),
    ("FAILED_ROWS", "Int64", 8, "ABS([EXPECTED] - [ACTUAL])"),
    ("STATUS", "V_String", 10, "IIF([ACTUAL] = [EXPECTED], 'PASS', 'FAIL')"),
    ("DETAIL", "V_WString", 1000,
     "'API $count ' + ToString([EXPECTED]) + ', records received ' + ToString([ACTUAL])"),
], note="PASS / FAIL", box=C4)
wf.connect(recon_sum, recon_f)
dq_union = wf.union(870, 1060, box=C4)
wf.connect(recon_f, dq_union, name="#1")
wf.connect(rej_dq, dq_union, name="#2")
dq_out = wf.select(960, 1060, ["RUN_ID", "ENTITY", "CHECK_NAME", ("EXPECTED", "", "Int64", 8),
                               ("ACTUAL", "", "Int64", 8), ("FAILED_ROWS", "", "Int64", 8), "STATUS", "DETAIL"],
                   note="DQ results layout", box=C4)
wf.connect(dq_union, dq_out)

# ---------------------------------------------------------------- 5 Load
C5 = wf.container("5  Load: Snowflake (landing -> Post-SQL MERGE)", 1440, 330, 300, 790, "#e9e9f6")
o_bus = wf.snowflake_output(1560, 380, "RAW.BUSINESS_LANDING", "Delete", "CALL CORE.MERGE_BUSINESS();",
                            note="RAW.BUSINESS_LANDING + MERGE", box=C5)
o_top = wf.snowflake_output(1560, 470, "RAW.BUSINESS_TOPIC_LANDING", "Delete", "CALL CORE.MERGE_BUSINESS_TOPIC();",
                            note="RAW.BUSINESS_TOPIC_LANDING + MERGE", box=C5)
o_ref = wf.snowflake_output(1560, 560, "CORE.TOPIC", "Delete", "", note="CORE.TOPIC (reference)", box=C5)
o_mem = wf.snowflake_output(1560, 660, "RAW.MEMBER_LANDING", "Delete", "CALL CORE.MERGE_MEMBER();",
                            note="RAW.MEMBER_LANDING + MERGE", box=C5)
o_aut = wf.snowflake_output(1560, 780, "RAW.BUSINESS_AUTHOR_LANDING", "Delete",
                            "CALL CORE.MERGE_BUSINESS_AUTHOR();", note="RAW.BUSINESS_AUTHOR_LANDING + MERGE", box=C5)
o_rej = wf.snowflake_output(1560, 990, "OPS.DQ_REJECTS", "Append", "", note="OPS.DQ_REJECTS", box=C5)
o_dq = wf.snowflake_output(1560, 1070, "OPS.DQ_RESULTS", "Append", "", note="OPS.DQ_RESULTS (alerts read this)",
                           box=C5)
wf.connect(fb_out, o_bus)
wf.connect(tp_out, o_top)
wf.connect(ref_s, o_ref)
wf.connect(fm_out, o_mem)
wf.connect(fr_out, o_aut)
wf.connect(rej_union, o_rej)
wf.connect(dq_out, o_dq)

OUT.write_text(wf.xml(), encoding="utf-8")
tools = sum(1 for i in wf.nodes if i not in wf.containers)
print(f"wrote {OUT.name}: {tools} tools/comments, {len(wf.containers)} containers, {len(wf.conns)} connections")
