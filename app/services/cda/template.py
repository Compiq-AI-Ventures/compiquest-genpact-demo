"""Fixed CD&A template content, transcribed from the reference document
``Genpact_CDA_2025_NEOs_Only_UI.docx``.

This module is the layout + narrative source of truth. It encodes
``body_blocks(ds)`` — the ordered list of content blocks for the full
proxy-style CD&A. Every numeric table is built from ``ds.neos`` (parsed from
the uploaded compensation file), so the figures change with each upload; all
narrative is fixed template text (standard proxy language), reproduced
verbatim from the reference document so the generated PDF reads "as per the
template". Cells the upload does not supply render as an em-dash.
"""

from __future__ import annotations

import re

from .schema import (
    NEO,
    Block,
    CDADataset,
    bullets,
    h1,
    h2,
    h3,
    note,
    p,
    spacer,
    stat_cards,
    table,
)

_PEER_GROUP = [
    "Accenture plc", "Akamai Technologies, Inc.", "Capgemini S.A.",
    "Cognizant Technology Solutions Corporation", "ExlService Holdings, Inc.",
    "Gartner, Inc.", "HCL Technologies Ltd.", "Infosys Limited",
    "International Business Machines Corporation",
    "Tata Consultancy Services Limited", "Wipro Limited", "WNS (Holdings) Limited",
]

_WHAT_WE_DO = [
    "Align our executive pay with performance",
    "Maintain a compensation clawback policy covering equity and cash incentive compensation of Section 16 officers in the event of an accounting restatement",
    "Make payouts under our annual cash bonus plan only if threshold Company performance is met",
    "Set challenging performance objectives for our PSU awards and annual cash bonus",
    "Maintain a meaningful equity ownership policy for the CEO (6x base salary) and other NEOs (1x base salary)",
    "Regularly review the relationship between NEO compensation and Company performance",
    "Include caps on individual payouts in short- and long-term incentive plans",
    "Maintain an independent compensation committee",
    "Hold an annual “say-on-pay” advisory vote",
    "Prohibit hedging and pledging of Company common shares",
    "Retain an independent compensation consultant",
    "Place a substantial majority of executive pay at risk",
    "Regularly evaluate our share utilization and the dilutive impact of equity awards",
    "Mitigate the potentially dilutive effect of equity awards through our share repurchase program",
    "Include restrictive covenants in equity award agreements, with a “clawback” of equity in certain circumstances",
    "Maintain a three-year performance period and cliff service vesting schedule for annual PSU awards",
]

_WHAT_WE_DONT = [
    "Offer contracts with multi-year guaranteed salary or bonus increases",
    "Provide guaranteed retirement benefits or contribute to non-qualified deferred compensation plans",
    "Provide tax gross-ups (except with respect to the reimbursement of relocation expenses)",
    "Provide excessive perquisites",
    "Grant equity awards with “single-trigger” change of control provisions",
    "Pay dividends or dividend equivalents on unvested equity awards",
    "Reprice or exchange underwater options without shareholder approval",
    "Maintain special retirement plans exclusively for executive officers",
    "Time the release of material non-public information to affect the value of executive compensation",
    "Allow short sales or purchases of equity derivatives of our common shares by officers or directors",
]

# (headline value, description) pairs — rendered as the cream/amber highlight
# cards shown in the reference template's "Key Financial Highlights" panel.
_FINANCIAL_HIGHLIGHTS = [
    ("$4.77 BILLION", "Net revenues, up 6.5% year-over-year (6.7% on a constant currency basis). 2024 revenues were $4.77 billion, up from $4.48 billion."),
    ("15%", "New bookings growth. New bookings in 2024 were $5.7 billion, up 15% from $5.0 billion in 2023."),
    ("$2.85 / $3.28", "Diluted EPS / Adjusted diluted EPS. Diluted EPS declined 16% to $2.85; adjusted diluted EPS grew 10% to $3.28."),
    ("10.8% / 14.7% / 17.1%", "Net income margin / income from operations margin / adjusted income from operations margin."),
    ("$361 MILLION", "Capital returned to shareholders — $253 million in share repurchases and $108 million in quarterly cash dividends."),
    ("$615 MILLION", "Cash generated from operations, up 25% from 2023."),
]


# ---------------------------------------------------------------------------
# NEO tables (every number flows from ds.neos, parsed from the upload)
# ---------------------------------------------------------------------------

def _neo_list_table(neos: list[NEO]) -> Block:
    return table(["Name", "Title"], [[n.name, n.title] for n in neos], numeric_from=99)


def _base_salary_table(neos: list[NEO]) -> Block:
    return table(
        ["Executive", "2023 Base Salary", "2024 Base Salary"],
        [[n.name, n.base_prior or "—", n.base_current] for n in neos],
    )


def _bonus_table(neos: list[NEO]) -> Block:
    return table(
        ["Executive", "2023 Payment", "2024 Target Bonus", "2024 Payment"],
        [[n.name, n.bonus_prior or "—", n.target_bonus, n.bonus_current or "—"]
         for n in neos],
    )


def _lti_table(neos: list[NEO]) -> Block:
    return table(
        ["Executive", "Total Target Value of 2024 LTI Awards ($)",
         "2024 PSU Target Shares (#)", "2024 RSUs (#)"],
        [[n.name, n.lti_target_value, n.psu_target_shares or "—", n.rsu_count or "—"]
         for n in neos],
    )


def _total_target_table(neos: list[NEO]) -> Block:
    return table(
        ["Executive", "2024 Base Salary", "2024 Target Bonus",
         "Total Target Value of 2024 Annual LTI Awards", "Total Annual Target Compensation"],
        [[n.name, n.base_current, n.target_bonus, n.annual_lti_target, n.total_target_comp]
         for n in neos],
    )


# ---------------------------------------------------------------------------
# Content-year shift
# ---------------------------------------------------------------------------
# The template prose is transcribed from Genpact's FY2024 proxy; the demo
# reports two cycles forward, so the content years in the *body text* are
# shifted +2 at render time (2022→2024, 2023→2025, … 2027→2029). This is a
# single simultaneous pass — a plain 2024→2026 then 2026→2028 would double-
# shift. 2029 is left as-is (it is already the forward-looking say-on-frequency
# year). The cover masthead and running footer are driven by ``ds.proxy_year``
# (2026), not this text, so they are unaffected.
_YEAR_SHIFT = {
    "2022": "2024", "2023": "2025", "2024": "2026", "2025": "2027",
    "2026": "2028", "2027": "2029",
}
_YEAR_RE = re.compile(r"\b202[234567]\b")


def _shift_years(text: str) -> str:
    return _YEAR_RE.sub(lambda m: _YEAR_SHIFT[m.group(0)], text)


def _remap_years(block: Block) -> Block:
    b = dict(block)
    if "text" in b:
        b["text"] = _shift_years(b["text"])
    if "items" in b:
        b["items"] = [_shift_years(x) for x in b["items"]]
    if "columns" in b:
        b["columns"] = [_shift_years(c) for c in b["columns"]]
    if "rows" in b:
        b["rows"] = [[_shift_years(c) for c in row] for row in b["rows"]]
    if b.get("type") == "statcards":
        b["cards"] = [
            {"value": _shift_years(c.get("value", "")),
             "text": _shift_years(c.get("text", ""))}
            for c in b["cards"]
        ]
    return b


# ---------------------------------------------------------------------------
# Full proxy-style CD&A body
# ---------------------------------------------------------------------------

def body_blocks(ds: CDADataset) -> list[Block]:
    neos = ds.neos
    b: list[Block] = []

    b += [
        h1("Executive Officer Compensation"),
        h2("Compensation Discussion and Analysis — Named Executive Officers (Excluding CEO)"),
        p("The compensation committee of the board of directors oversees our executive officer compensation program. In this role, the compensation committee reviews and approves all compensation decisions relating to our named executive officers. This Compensation discussion and analysis section discusses the compensation policies and programs for our Chief Financial Officer (referred to as our CFO) and our other most highly paid executive officers as determined under the rules of the SEC, excluding our Chief Executive Officer. Such individuals are referred to in this document as our named executive officers (“NEOs”)."),
        p("Our named executive officers covered in this document for 2024 are:"),
        _neo_list_table(neos),
        note("This document excludes compensation information specific to Genpact’s Chief Executive Officer (Balkrishan “BK” Kalra) and former Chief Executive Officer (N.V. “Tiger” Tyagarajan)."),
    ]

    b += [
        h3("2024 Key Financial Highlights"),
        p("In 2024, we sharpened our focus on execution, deepened client relationships and accelerated growth in high-impact areas. Our strong financial results for the year reflect our disciplined focus in these areas."),
        stat_cards(_FINANCIAL_HIGHLIGHTS),
        p("Despite continued macroeconomic uncertainty and geopolitical tensions affecting the markets in which we and our clients operate during 2024, our 2024 financial performance exceeded our expectations in several areas, including a 15% increase in new bookings on top of record new bookings in the prior year as well as strong net revenue and adjusted diluted EPS growth, which led to higher-than-target performance against the goals set for the first year under our three-year 2024 performance share unit (“PSU”) awards. The 2024 bonus pool for our NEOs funded at target, reflecting the continued rigor of the goals we set for our performance-based compensation plans."),
    ]

    b += [
        h3("Compensation Objectives"),
        p("The primary objectives of our compensation program for our executives, including our named executive officers, are to attract, motivate and retain highly talented individuals. Our compensation program is designed to incentivize and reward the achievement of our annual, long-term and strategic goals, such as growing revenues and improving profitability. It is also designed to align the interests of our executives, including our named executive officers, with those of our shareholders by rewarding performance that exceeds our target goals, with the ultimate objective of increasing shareholder value."),
        p("Our compensation committee is responsible for overseeing the goals and objectives of our executive compensation plans and programs. The compensation committee bases our executive compensation programs on the same objectives that guide us in administering the compensation programs for all of our employees globally:"),
        bullets([
            "Compensation is based on the individual’s level of job responsibility.",
            "Compensation reflects the value of the job in the marketplace.",
            "Compensation programs are designed to incentivize and reward performance, both on an individual and Company basis.",
        ]),
        p("Our compensation committee considers risk when developing our compensation program and believes that the design of our current compensation program does not encourage excessive or inappropriate risk taking. For example, we believe that our annual cash bonus plan, which is based on a number of different performance measures together with a meaningful cap on the potential payout, deters executives from focusing exclusively on the specific financial metrics that might encourage excessive short-term risk taking. For 2024, our named executive officers were also granted PSU awards tied to the attainment of multiple performance goals over a three-year period (with a maximum payout of 240% of target) and restricted share unit (“RSU”) awards that vest ratably in equal one-third installments over a three-year period."),
        p("In addition, we have implemented several policies that mitigate excessive risk-taking, including a clawback of compensation in the event of certain accounting restatements, share ownership guidelines and a prohibition on hedging or pledging our shares."),
    ]

    b += [
        h3("Executive Compensation Practices"),
        p("We strive to maintain sound governance standards and compensation practices by continually monitoring the evolution of “best practices.” As in prior years, we incorporated many best practices into our 2024 compensation programs, including the following:"),
        p("What we do:"),
        bullets(_WHAT_WE_DO),
        p("What we don’t do:"),
        bullets(_WHAT_WE_DONT),
    ]

    b += [
        h3("Pay for Performance Philosophy"),
        p("The core objective of our executive officer compensation program is to align pay and performance. We believe that as an employee’s level of responsibility increases, so should the proportion of total compensation opportunity that is structured in the form of short-term and long-term incentive opportunities. The compensation of our named executive officers for 2024 reflects both our 2024 performance and our commitment to providing executive compensation opportunities that are linked to Company performance, including progress on long-term strategic goals and shareholder value creation."),
        p("The material components of our compensation are (i) a fixed base salary and (ii) variable compensation comprised of (A) short-term incentive compensation under our performance-based annual cash bonus plan and (B) long-term incentive compensation in the form of equity awards, which since 2023 have been granted as annual PSU and RSU awards."),
        p("The table below reflects the target mix of compensation components for our NEOs during fiscal year 2024. In 2024, approximately 60% (on average) of the total target compensation of our named executive officers was in the form of long-term incentive compensation."),
        table(
            ["Other NEOs 2024 Compensation Mix", "Percent"],
            [
                ["Base Salary", "20%"],
                ["Performance Bonus", "20%"],
                ["RSUs", "20% (approx.)"],
                ["PSUs", "40% (approx.)"],
                ["Total Compensation at Risk", "80%"],
                ["Total Equity", "60% (approx.)"],
            ],
        ),
    ]

    b += [
        h3("Shareholder Engagement"),
        p("We welcome and value the views and insights of our shareholders. We have ongoing communications with our shareholders in the normal course of business and evaluate all shareholder feedback. Leading up to and following our 2024 annual meeting, at which 91% of the shares voted on our say-on-pay proposal were voted in favor of our 2023 executive compensation practices, we conducted extensive shareholder outreach to better understand our shareholders’ perspectives on our compensation practices and to solicit their feedback. In the second quarter of 2024, we contacted shareholders owning more than 70% of our outstanding shares, including our top 10 shareholders, and we had discussions with shareholders representing approximately 14% of our total shares then outstanding."),
    ]

    b += [
        h3("Shareholder Feedback and Responsiveness"),
        p("Based on shareholder feedback, our compensation committee evaluated and implemented several significant changes to our compensation practices in our 2023 compensation program, including eliminating time-based option grants and replacing them with annual RSU grants, increasing the length of the performance period for our PSU awards from one year to three years and including a relative TSR modifier in our PSU awards. Given the significant changes made in the 2023 compensation program and the positive shareholder feedback, no significant changes were made in our 2024 compensation practices."),
        table(
            ["Pay Practice", "2022 and Prior Years", "2023 Onwards"],
            [
                ["Length of performance period for annual PSU awards", "One year", "Three years"],
                ["Time-based equity vehicle", "Periodic multi-year options", "Annual RSUs with graded vesting"],
                ["Relative performance metric", "None", "rTSR modifier in PSUs"],
            ],
            numeric_from=99,
        ),
    ]

    b += [
        h3("Our Process"),
        p("Our compensation committee is responsible for reviewing the performance and potential of each of our executive officers, including our named executive officers, approving the compensation level of each of our executive officers, establishing criteria for granting equity awards, approving such grants and combining the compensation elements for each executive in a manner we believe best fulfills the objectives of our compensation program. The compensation committee works closely with our CEO, discussing the Company’s overall performance, the CEO’s performance and his evaluation of and compensation recommendations for the other named executive officers."),
        p("Base salaries and target annual bonuses for 2024 were reviewed at the end of 2023 and adjustments were approved by the compensation committee for our named executive officers. Base salary and target bonus increases for 2024 were made effective January 1, 2024. The performance goals for our 2024 annual bonus plan and the 2024 PSU awards were approved by the compensation committee in early 2024 based on expected financial performance for the full year and the Company’s strategic and operational priorities."),
    ]

    b += [
        h3("Role of Consultants and Advisors in Compensation Decisions"),
        p("The compensation committee has the authority to retain and terminate an independent third-party compensation consultant and to obtain independent advice and assistance from internal and external legal, accounting and other advisors. In establishing the 2024 compensation for our named executive officers, the compensation committee reviewed, for reference, materials prepared by Aon, a compensation consulting firm, showing peer group compensation levels and practices for the peer group set forth below."),
        p("Peer group companies for named executive officers:"),
        bullets(_PEER_GROUP),
    ]

    b += [
        h3("2024 Target Pay Mix and Pay Positioning"),
        p("The compensation committee annually reviews the total direct compensation and pay mix for the CEO and each other named executive officer. While we do not have any pre-established allocation of the target pay mix, the compensation committee’s overall intent is to emphasize the variable, performance-based components of pay and, accordingly, we allocate a significant percentage of targeted total compensation in the form of PSUs and our annual performance-based bonus plan."),
        p("For our named executive officers, approximately 80% of target total direct compensation for 2024 was in the form of variable compensation, the ultimate value of which depends on achievement of annual and long-term financial goals or share price performance. Only 20% of our NEOs’ 2024 target compensation was in the form of fixed pay."),
    ]

    b += [
        h3("Say-on-Pay Vote"),
        p("Each year, our compensation committee considers the outcome of our annual shareholder advisory vote on executive compensation. At our 2024 annual meeting of shareholders, approximately 91% of the votes cast were in favor of the compensation of our named executive officers."),
        h3("Frequency of “Say-on-Pay” Shareholder Advisory Vote"),
        p("Based on the results of the “say-on-frequency” vote held at our 2023 annual meeting, at which approximately 98.5% of the votes submitted were in favor of holding an annual shareholder advisory “say-on-pay” vote, our board of directors has decided that shareholder advisory “say-on-pay” votes will occur annually. Our next “say-on-frequency” vote will be held at our 2029 annual meeting."),
    ]

    b += [
        h3("Compensation Components"),
        p("For fiscal 2024, our executive compensation program had three primary components, in addition to certain benefits and perquisites:"),
        bullets([
            "Base salary;",
            "Short-term, performance-based incentive compensation, or our annual cash bonus plan; and",
            "Long-term, performance- and time-based equity compensation in the form of PSUs and RSUs.",
        ]),
    ]

    b += [
        h3("Base Salary"),
        p("Base salary is provided to ensure that we are able to attract and retain high-quality executives. It is intended to provide a fixed level of overall compensation that does not vary annually based on performance or changes in shareholder value. Our compensation committee reviews the base salaries of our executives annually, considering the importance of the executive’s role, the performance and potential of the executive, general Company performance, market practices in the relevant country and the executive’s current base salary relative to benchmarking data for the peer group companies."),
        _base_salary_table(neos),
    ]

    b += [
        h3("Annual Cash Bonus"),
        p("Annual cash bonuses under our cash bonus plan are designed to reward our executives for Company performance and their individual performance during the most recent year. Annual bonuses are payable only if threshold performance is attained. For 2024 the compensation committee established a bonus pool, funded based on the level of attainment of Company performance metrics, and established target bonuses for each NEO. For 2024, our named executive officers’ target bonuses were 100% of their base salaries."),
        p("The 2024 bonus pool was funded based on the Company’s 2024 adjusted income from operations (“AOI”) margin, revenue and employee engagement score performance, weighted 45%, 45% and 10%, respectively:"),
        table(
            ["Performance Goal (Weighting)", "Threshold", "Target", "Outstanding"],
            [
                ["AOI margin (45%)", "98%", "100%", "102%"],
                ["Revenue (45%)", "99%", "100%", "104%"],
                ["Employee engagement score (10%)", "92%", "100%", "108%"],
            ],
        ),
        table(
            ["Bonus Pool Performance Level", "Company Multiplier (% of Total Target Bonuses)"],
            [["Threshold", "50%"], ["Target", "100%"], ["Outstanding", "200%"]],
        ),
        p("For 2024, we achieved higher-than-target performance on the revenue and employee engagement score goals and lower-than-target but higher than threshold performance on the AOI margin goal, resulting in a Company Multiplier of approximately 100%. The 2024 bonus for each named executive officer was determined as follows: 2024 target bonus multiplied by individual scorecard achievement (0-150%) multiplied by the 2024 Bonus Payment Multiplier of approximately 106%."),
        _bonus_table(neos),
    ]

    b += [
        h3("Equity-Based Compensation"),
        p("Our equity-based compensation program is designed to attract and retain highly qualified individuals and to align the long-term interests of our executives with those of our shareholders. For 2024, the annual equity component was comprised of PSUs and RSUs. The RSUs vest with respect to one-third of the shares underlying the award on January 10th each year from 2025 through 2027, subject to continued service. In March 2024, we granted additional one-time retention RSUs to Mr. Weiner ($2 million) and to Messrs. Mehta and Nanduru and Ms. Vashisht ($1.5 million each) in connection with the CEO transition in early 2024."),
        p("Under the 2024 PSU awards, each named executive officer is eligible to receive shares based on the Company’s attainment of specified performance goals over a three-year performance period and continued service through March 10, 2027. The performance goals for the 2024 PSUs were adjusted diluted earnings per share (“Adjusted EPS”), weighted 50%, and revenue, weighted 50%, and include a relative TSR modifier adjusting the aggregate performance percentage from 0.8x to 1.2x, so the awards may ultimately convert into 0% to 240% of the target number of shares."),
        table(
            ["Performance Level", "Vesting Percentage"],
            [["Below Threshold", "0%"], ["Threshold", "50%"], ["Target", "100%"], ["Outstanding", "200%"]],
        ),
        table(
            ["Performance Goal (Weighting)", "Threshold", "Target", "Outstanding"],
            [["Adjusted EPS (50%)", "99%", "100%", "105%"], ["Revenue (50%)", "98%", "100%", "103%"]],
        ),
        p("Messrs. Mehta and Nanduru and Ms. Vashisht were each also granted a one-time supplemental PSU award with a target value of $500,000 in June 2024, with the same performance and vesting conditions as the annual 2024 PSU awards. The total 2024 long-term incentive awards were as follows:"),
        _lti_table(neos),
    ]

    b += [
        h3("Total Annual Target Compensation"),
        _total_target_table(neos),
    ]

    b += [
        h3("Benefits and Perquisites"),
        p("We provide other benefits to our named executive officers that are generally available to other employees in the country in which the named executive officer is located, along with certain modest perquisites consistent with market practice. Our U.S.-based named executive officers are also eligible to participate in the Genpact LLC Executive Deferred Compensation Plan."),
        h3("Change of Control and Severance Benefits"),
        p("Under the terms of our equity incentive plan, in the event of a change of control, the PSU and RSU awards granted to our named executive officers in 2024 will accelerate unless the awards are assumed, continued or substituted. If assumed, continued or substituted, any such awards are subject to accelerated vesting in the event of the executive officer’s termination without cause within 24 months following a change of control. We have also entered into employment agreements with our named executive officers which provide for certain payments and benefits in the event of a termination of employment."),
        h3("Policies and Practices Related to the Grant of Equity Awards"),
        p("We grant equity awards, including PSUs and RSUs, to our employees and directors on an annual basis, and may also grant equity awards upon hire or promotion or for retention purposes. We currently do not grant stock options, stock appreciation rights or similar option-like instruments. During the last fiscal year, neither the board of directors nor the compensation committee took material nonpublic information into account when determining the timing or terms of equity awards."),
        h3("Share Ownership Guidelines"),
        p("Under our share ownership guidelines, all of our executive officers are required to acquire and hold Genpact common shares with a value of at least (i) in the case of the CEO, six times his base salary, and (ii) in the case of every other executive officer, such officer’s base salary. Each executive officer has a five-year phase-in period to meet the ownership requirements. As of December 31, 2024, all of our NEOs were in compliance with the ownership requirement applicable to them."),
        h3("Compensation Clawback Policy"),
        p("Effective October 2, 2023, our board adopted a compensation clawback policy that covers each of our Section 16 officers and is intended to comply with the requirements of Section 10D of the Exchange Act and Section 303A.14 of the NYSE Listed Company Manual with respect to the mandatory recovery of certain specified financial-based incentive compensation in connection with specified accounting restatements."),
        h3("Insider Trading Policy"),
        p("We have adopted an insider trading policy containing policies and procedures governing the purchase, sale, and/or other transactions of our securities by our directors, officers, employees and consultants, reasonably designed to promote compliance with insider trading laws, rules, and regulations and any applicable listing standards."),
        h3("IRC Section 162(m) Compliance"),
        p("Section 162(m) of the Internal Revenue Code limits the amount that we may deduct from our federal income taxes for compensation paid to certain executive officers to $1 million per executive officer per year. The compensation committee will continue to consider the tax impact of the Company’s compensation programs but reserves the right to pay compensation that is not tax deductible."),
    ]

    return [_remap_years(block) for block in b]


# The exec-summary section is data-driven (assembled in ``__init__.py``); the
# spacer here is a convenience for callers stitching sections together.
def section_gap() -> Block:
    return spacer(4.0)
