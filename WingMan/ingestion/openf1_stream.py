"""OpenF1 ingestion client (placeholder)
Polls OpenF1 endpoints and emits state vectors to the pipeline.
"""

import asyncio

async def run():
    # TODO: implement OpenF1 polling at 4Hz, handle stale data
    print("openf1_stream placeholder")

if __name__ == '__main__':
    asyncio.run(run())
