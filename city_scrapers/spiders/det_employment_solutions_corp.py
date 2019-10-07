import re
from collections import defaultdict
from datetime import datetime
from io import BytesIO

import scrapy
from city_scrapers_core.constants import BOARD, COMMITTEE, NOT_CLASSIFIED
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from PyPDF2 import PdfFileReader


class DetEmploymentSolutionsCorpSpider(CityScrapersSpider):
    name = "det_employment_solutions_corp"
    agency = "Detroit Employment Solutions Corporation"
    timezone = "America/Detroit"
    allowed_domains = ["www.descmiworks.com"]
    start_urls = ["https://www.descmiworks.com/about-us/public-meetings/"]
    location = {
        "name": "DESC",
        "address": "440 E. Congress, Detroit, MI 48226",
    }

    def __init__(self, *args, **kwargs):
        self.meeting_dates = []
        self.docs_link = ""
        super().__init__(*args, **kwargs)

    def parse(self, response):
        # `parse` should always `yield` Meeting items.
        # Change the `_parse_title`, `_parse_start`, etc methods to fit your scraping
        # needs.

        schedule_link = ""
        for link in response.css("a"):
            link_text = " ".join(link.css("*::text").extract())
            if "Schedule" in link_text:
                schedule_link = link.attrib["href"]
            elif "Minutes" in link_text:
                self.docs_link = link.attrib["href"]
        if schedule_link and self.docs_link:
            yield scrapy.Request(
                response.urljoin(schedule_link), callback=self._parse_schedule, dont_filter=True
            )
        else:
            raise ValueError("Required links not found")

    def _parse_schedule(self, response):
        # Parse PDF and then yield to documents page""""

        self._parse_schedule_pdf(response)
        yield scrapy.Request(
            response.urljoin(self.docs_link), callback=self._parse_documents, dont_filter=True
        )

    def _parse_schedule_pdf(self, response):
        """Parse dates and details from schedule PDF"""
        pdf_obj = PdfFileReader(BytesIO(response.body))
        # need to get all pages of pdf schedule
        number_of_pages = pdf_obj.getNumPages()
        pdf_text = ""
        for page_number in range(number_of_pages):
            pdf_text += pdf_obj.getPage(page_number).extractText()

        # Remove duplicate characters split onto separate lines
        clean_text = re.sub(r"([A-Z0-9:\n ]{2})\1", r"\1", pdf_text, flags=re.M)
        # Join lines where there's only a single character, then remove newlines
        clean_text = re.sub(r"(?<=[A-Z0-9:])\n", "", clean_text, flags=re.M).replace("\n", " ")
        # Remove duplicate spaces
        clean_text = re.sub(r"\s+", " ", clean_text)

        year_str = re.search(r"\d{4}", clean_text).group()
        self._validate_location(clean_text)

        time_strs = re.findall(
            r"\d{1,2}[:]\d{2}\s*[a|p|A|P][m|M].*?\d{1,2}[:]\d{2}\s*[a|p|A|P][m|M]", clean_text
        )

        time_tuples = []
        for time_str in time_strs:
            time_tuples.append(tuple(re.findall(r"\d{1,2}[:]\d{2}\s*[a|p|A|P][m|M]", time_str)))
        # extra space to deal with situations like J uly (which is present in test case)
        date_strs = re.findall(
            r"[J|F|M|A|S|O|N|D][a-z|]{0,8}\s?[a-z]{1,8}\s+\d{1,2}(?!\d)", clean_text
        )

        if len(time_tuples) != len(date_strs):
            raise ValueError("Not all dates can be matched with start and end time")

        for i in range(len(time_strs)):
            date_str = date_strs[i]
            try:
                datetime.strptime(
                    "{} {} {}".format(date_str, year_str, '10:30 am'), "%B %d %Y %I:%M %p"
                )
            except ValueError:
                date_str = date_str.replace(" ", "", 1)
            time_str = time_tuples[i]
            start_time = time_str[0]
            end_time = time_str[1]
            self.meeting_dates.append({
                'start': self._parse_datetime(start_time, date_str, year_str),
                'end': self._parse_datetime(end_time, date_str, year_str)
            })

    def _parse_documents(self, response):
        """Parse agenda and minutes page"""
        link_map = self._parse_link_map(response)
        title_map = self._parse_title_map(response)
        for date in self.meeting_dates:
            title_in = self._parse_title(date, title_map)
            meeting = Meeting(
                title=title_in,
                description="",
                classification=self._parse_classification(title_in),
                start=date['start'],
                end=date['end'],
                all_day=False,
                time_notes="",
                location=self.location,
                links=link_map[(date['start'].month, date['start'].year)],
                source=self.start_urls[0],
            )

            meeting["status"] = self._get_status(meeting)
            meeting["id"] = self._get_id(meeting)

            yield meeting

    def _parse_title(self, date, title_map):
        """Generate title from title map or return generic title"""
        if title_map[(date['start'].month, date['start'].year)]:
            return title_map[(date['start'].month, date['start'].year)]
        return "Detroit Employment Solutions Corporation"

    def _parse_classification(self, title):
        """Parse or generate classification from allowed options."""
        if "Board" in title:
            return BOARD

        if "Committee" in title:
            return COMMITTEE

        return NOT_CLASSIFIED

    def _parse_datetime(self, time_str, date_str, year_str):
        """Parse start datetime as a naive datetime object."""
        return datetime.strptime(
            "{} {} {}".format(date_str, year_str, time_str), "%B %d %Y %I:%M %p"
        )

    def _parse_link_map(self, response):
        """Parse or generate links. Returns a dictionary of month, year tuples and link lists"""
        link_map = defaultdict(list)

        title_divs = response.css('div[class="meeting-min_inner-wrapper"]')
        for div in title_divs:
            date = div.css("p::text").extract_first().strip()
            date = date.split('/')
            link_start = datetime(int(date[2]), int(date[0]), int(date[1]))
            link = div.css("a").xpath('@href').get()
            print(link)
            link_map[(link_start.month, link_start.year)].append({"title": "Minutes", "href": link})

        return link_map

    def _parse_title_map(self, response):
        """parse or generate titles Returns a dictionary of month, year tuples and title lists"""
        title_map = defaultdict(list)

        title_divs = response.css('div[class="meeting-min_inner-wrapper"]')
        for div in title_divs:
            date = div.css("p::text").extract_first().strip()
            date = date.split('/')
            title_date = datetime(int(date[2]), int(date[0]), int(date[1]))
            title_text = div.css("h3::text").extract_first().strip()
            title_map[(title_date.month, title_date.year)] = title_text

        return title_map

    def _validate_location(self, text):
        if "440" not in text:
            raise ValueError("Meeting location has changed")