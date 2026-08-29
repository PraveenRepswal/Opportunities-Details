import asyncio
import json
from scrapers.greatyop import GreatYopScraper
from scrapers.scholars4dev import Scholars4Dev
from scrapers.scholarshipscorner import ScholarshipsCorner
from scrapers.youthop import YouthOP
from scrapers.opportunitiescorner import OpportunitiesCorners
from scrapers.opportunitiesforyouth import OpportunitiesForYouth
from config import settings
import icecream as ic

class CombinedScraper:
    def __init__(self, days_back=settings.scraper.days_back, threshold=settings.scraper.score_threshold):
        self.days_back = days_back
        self.threshold = threshold
        self.enrichment_task = None
        
    async def run_all_scrapers(self):
        """Run all scrapers concurrently and combine results"""
        ic.ic("Starting combined opportunity scraping pipeline...")
        
        
        # Create instances of all scrapers
        youthop_scraper = YouthOP(
            index_url=settings.scraper.urls["youthop"],
            days_back=self.days_back,
            threshold=self.threshold
        )
        greatyop_scraper = GreatYopScraper(
            index_url=settings.scraper.urls["greatyop"],
            days_back=self.days_back,
            threshold=self.threshold
        )
        scholars4dev_scraper = Scholars4Dev(
            index_url=settings.scraper.urls["scholars4dev"],
            days_back=self.days_back,
            threshold=self.threshold
        )
        scholarshipscorner_scraper = ScholarshipsCorner(
            index_url=settings.scraper.urls["scholarshipscorner"],
            days_back=self.days_back,
            threshold=self.threshold
        )
        opportunitiescorner = OpportunitiesCorners(
            index_url=settings.scraper.urls["opportunitiescorner"],
            days_back=self.days_back,
            threshold=self.threshold
        )
        opportunitiesforyouth = OpportunitiesForYouth(
            index_url=settings.scraper.urls["opportunitiesforyouth"],
            days_back=self.days_back,
            threshold=self.threshold
        )
    
        ic.ic("Fetching data from all sources concurrently...")

        youthop_data, greatyop_data, scholars4dev_data, scholarshipscorner_data, opportunitiescorner_data, opportunitiesforyouth_data = await asyncio.gather(
            asyncio.wait_for(youthop_scraper.getting_data(), timeout=70),
            asyncio.wait_for(greatyop_scraper.getting_data(), timeout=60),
            asyncio.wait_for(scholars4dev_scraper.getting_data(), timeout=60),
            asyncio.wait_for(scholarshipscorner_scraper.getting_data(), timeout=60),
            asyncio.wait_for(opportunitiescorner.getting_data(), timeout=60),
            asyncio.wait_for(opportunitiesforyouth.getting_data(), timeout=60),
            return_exceptions=True  # Don't fail if one scraper fails
        )

        if isinstance(youthop_data, Exception):
            ic.ic(f"YouthOP failed: {youthop_data}")
            youthop_data = []
        
        if isinstance(greatyop_data, Exception):
            ic.ic(f"GreatYopScraper failed: {greatyop_data}")
            greatyop_data = []

        if isinstance(scholars4dev_data, Exception):
            ic.ic(f"Scholars4Dev failed: {scholars4dev_data}")
            scholars4dev_data = []
        
        if isinstance(scholarshipscorner_data, Exception):
            ic.ic(f"ScholarshipsCorner failed: {scholarshipscorner_data}")
            scholarshipscorner_data = []
        
        if isinstance(opportunitiesforyouth_data, Exception):
            ic.ic(f"OpportunitiesForYouth failed: {opportunitiesforyouth_data}")
            opportunitiesforyouth_data = []

        if isinstance(opportunitiescorner_data, Exception):
            ic.ic(f"OpportunitiesCorners failed: {opportunitiescorner_data}")
            opportunitiescorner_data = []

        # Combine the results
        combined_results = youthop_data + greatyop_data + scholars4dev_data + scholarshipscorner_data + opportunitiescorner_data + opportunitiesforyouth_data

        ic.ic("type of combined_results:", type(combined_results))

        with open("scraped_data.txt", "w", encoding='utf-8') as f:
            json.dump(combined_results, f, indent=2)

        # Inline rules-based metadata extraction (fast, deterministic)
        if settings.scraper.extract_metadata:
            from backend.metadata_extractor import extract_metadata_rules
            for item in combined_results:
                item["metadata"] = extract_metadata_rules(
                    item.get("name") or item.get("title") or "",
                    item.get("content") or "",
                )

        # Upsert into SQLite database
        row_ids = []
        try:
            from backend.database import upsert_opportunities
            row_ids = upsert_opportunities(combined_results)
            ic.ic(f"Upserted {len(row_ids)} opportunities to SQLite database.")
        except Exception as err:
            ic.ic(f"Error saving to SQLite database: {err}")

        # Background LLM enrichment for opportunities with missing metadata fields
        if settings.scraper.llm_enrichment and row_ids:
            from backend.metadata_extractor import enrich_missing_metadata, find_missing_fields
            incomplete = [
                (row_id, item)
                for row_id, item in zip(row_ids, combined_results)
                if row_id != -1 and find_missing_fields(item.get("metadata"))
            ]
            if incomplete:
                ic.ic(f"Scheduling LLM metadata enrichment for {len(incomplete)} opportunities...")
                self.enrichment_task = asyncio.create_task(enrich_missing_metadata(incomplete))

        print(f"\n{'='*60}")
        print("SCRAPING SUMMARY")
        print(f"{'='*60}")
        print(f"Source: YouthOP              | Items: {len(youthop_data):3d}")
        print(f"Source: GreatYop             | Items: {len(greatyop_data):3d}")
        print(f"Source: Scholars4Dev         | Items: {len(scholars4dev_data):3d}")
        print(f"Source: ScholarshipsCorner   | Items: {len(scholarshipscorner_data):3d}")
        print(f"Source: OpportunitiesCorners | Items: {len(opportunitiescorner_data):3d}")
        print(f"Source: OpportunitiesForYouth| Items: {len(opportunitiesforyouth_data):3d}")
        print(f"{'='*60}")
        print(f"Total Combined Opportunities | Items: {len(combined_results):3d}")
        print(f"{'='*60}")
        print("Output File: scraped_data.txt & SQLite Database")
        print(f"{'='*60}\n")

        return combined_results

    async def await_enrichment(self):
        """Wait for the background LLM metadata enrichment task, if scheduled."""
        if self.enrichment_task is not None:
            try:
                await self.enrichment_task
            except Exception as err:
                ic.ic(f"Metadata enrichment failed: {err}")
            finally:
                self.enrichment_task = None

async def main():
    scraper = CombinedScraper(
        days_back=30,
        threshold=0.7
    )
    results = await scraper.run_all_scrapers()
    await scraper.await_enrichment()
    return results

if __name__ == "__main__":
    asyncio.run(main())