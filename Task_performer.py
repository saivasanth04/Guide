import asyncio

from orchestrator import run_task


async def main():
    result = await run_task(
        """
Build a calculator application using React.

Requirements:
- Addition
- Subtraction
- Multiplication
- Division
- Decimal numbers
- Clear/reset
- Display input and result
- Handle division by zero
- Keep the implementation simple
- Ensure the React application builds successfully
"""
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())