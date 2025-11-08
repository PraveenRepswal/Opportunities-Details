import asyncio
import json
import time
from main import OpportunitiesCorners
from opportunitiesforyouth import OpportunitiesForYouth
import icecream as ic


class CombinedScraper:
    def __init__(self, days_back=30, threshold=0.7):
        self.days_back = days_back
        self.threshold = threshold
        self.start_time = time.time()
        
    async def run_both_scrapers(self):
        """Run both scrapers concurrently and combine results"""
        ic.ic("Starting combined scraping process")
        
        # Create instances of both scrapers
        corners_scraper = OpportunitiesCorners(
            sitemap_url='https://opportunitiescorners.com/post-sitemap.xml',
            days_back=self.days_back,
            threshold=self.threshold
        )
        
        youth_scraper = OpportunitiesForYouth(
            sitemap_url='https://opportunitiesforyouth.org/sitemap-1.xml',
            days_back=self.days_back,
            threshold=self.threshold
        )
        
        # Run both scrapers concurrently
        ic.ic("Fetching data from both sources concurrently...")
        
        # Use asyncio.gather to run both scrapers at the same time
        corners_data, youth_data = await asyncio.gather(
            corners_scraper.getting_data(),
            youth_scraper.getting_data(),
            return_exceptions=True  # Don't fail if one scraper fails
        )
        
        # Handle any exceptions
        if isinstance(corners_data, Exception):
            ic.ic(f"OpportunitiesCorners failed: {corners_data}")
            corners_data = []
        
        if isinstance(youth_data, Exception):
            ic.ic(f"OpportunitiesForYouth failed: {youth_data}")
            youth_data = []
        
        # Combine the results
        combined_results = []
        
        # Add source information to each item
        # combined_results = corners_data + youth_data
        # Replace lines 55-59 with:
        combined_results.extend(corners_data)
        combined_results.extend(youth_data)
        ic.ic("type of combined_results:", type(combined_results))
        
        # Save combined results
        with open("combined_opportunities.txt", "w", encoding='utf-8') as f:
            json.dump(combined_results, f, indent=2)
        
        # Calculate statistics
        end_time = time.time()
        total_time = end_time - self.start_time
        
        print(f"\n{'='*60}")
        print(f"SCRAPING SUMMARY")
        print(f"{'='*60}")
        print(f"Source: OpportunitiesCorners    | Items: {len(corners_data):3d}")
        print(f"Source: OpportunitiesForYouth   | Items: {len(youth_data):3d}")
        print(f"{'='*60}")
        print(f"Total Combined Opportunities    | Items: {len(combined_results):3d}")
        print(f"{'='*60}")
        print(f"Execution Time: {total_time:.2f} seconds")
        print(f"Output File: combined_opportunities.txt")
        print(f"{'='*60}\n")
        
        return combined_results


async def main():
    """Main entry point"""
    scraper = CombinedScraper(days_back=30, threshold=0.7)
    results = await scraper.run_both_scrapers()
    return results


if __name__ == '__main__':
    asyncio.run(main())
