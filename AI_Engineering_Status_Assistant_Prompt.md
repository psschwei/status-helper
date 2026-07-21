# AI Engineering Status Assistant

I want to build an AI-powered engineering status assistant for technical
leads and engineering managers.

The goal is to reduce the time spent manually gathering project status
while producing better summaries, identifying blockers, and helping
engineering leadership focus their attention where it is most valuable.

This is **not** an employee productivity tracker. The purpose is to
improve engineering visibility and reduce coordination overhead.

------------------------------------------------------------------------

# Core Questions

The application should help answer questions like:

-   What happened this week?
-   What is currently in progress?
-   What is blocked?
-   What projects are at risk?
-   What is each engineer currently working on?
-   Where should I spend my time as a tech lead?
-   Which projects or repositories need attention?

------------------------------------------------------------------------

# Product Vision

The application should aggregate engineering activity across **multiple
repositories** and present a unified view of engineering work.

It must support repositories hosted on:

-   GitHub.com
-   GitHub Enterprise Server

The architecture should make it easy to add additional GitHub
organizations, Enterprise instances, and eventually other engineering
systems without major redesign.

The application should treat repositories as first-class entities rather
than assuming a single repository.

------------------------------------------------------------------------

# Guiding Principles

Build this like a real product.

Prioritize:

-   Clean architecture
-   Maintainability
-   Testability
-   Strong typing
-   Extensibility
-   Good UX
-   Incremental delivery

Avoid building unnecessary abstractions until they are needed.

Prefer simple solutions over clever ones.

------------------------------------------------------------------------

# Development Process

This project should be developed **incrementally in vertical slices**.

Do **not** scaffold the entire application before implementing
functionality.

Instead, each iteration should produce a working feature from end to
end.

For every feature:

1.  Decide on the smallest valuable increment.
2.  Implement backend functionality.
3.  Persist any required data.
4.  Expose it through the API.
5.  Display it in the UI.
6.  Verify it works before moving on.

I would rather have ten completed small features than a large unfinished
framework.

At the end of every implementation step:

-   Explain why the design was chosen.
-   Identify tradeoffs.
-   Recommend the next smallest valuable feature.

Help keep the project scoped appropriately. If I'm trying to build too
much at once, suggest a smaller increment.

------------------------------------------------------------------------

# Initial Data Sources

For the MVP, use GitHub APIs.

Support multiple repositories across multiple GitHub installations.

Collect information such as:

-   Issues
-   Pull Requests
-   Reviews
-   Review Requests
-   Commits
-   Labels
-   Milestones
-   Assignees
-   Projects (if available)
-   Branches (where useful)

Design the ingestion layer so additional data sources can be added
later, including:

-   Slack
-   Jira
-   Linear
-   Google Docs
-   Confluence
-   Calendar systems

------------------------------------------------------------------------

# MVP Features

## Project Summary

Generate:

-   Completed work
-   Active work
-   Blocked work
-   Risks
-   Upcoming milestones
-   Suggested engineering status update

## Repository View

For each repository display:

-   Active pull requests
-   Active issues
-   Blocked work
-   Stale work
-   Recent activity
-   Repository health summary

## Engineer View

For each engineer display:

-   Current work
-   Recently completed work
-   Open pull requests
-   Pending reviews
-   Reviews they owe
-   Reviews blocking their work
-   Current blockers
-   AI-generated status summary

The emphasis should be helping managers understand context---not
measuring productivity.

## Team Dashboard

Show:

-   Projects requiring attention
-   Engineers waiting on reviews
-   Aging pull requests
-   Stale issues
-   Milestones at risk
-   Cross-repository activity
-   Overall engineering summary

------------------------------------------------------------------------

# AI Responsibilities

Use deterministic code for:

-   Fetching data
-   Filtering
-   Aggregation
-   Metrics
-   Caching

Use the LLM for:

-   Summarization
-   Trend detection
-   Blocker identification
-   Priority recommendations
-   Status report generation
-   Dependency reasoning
-   Natural-language explanations

Avoid using the LLM where deterministic logic is sufficient.

------------------------------------------------------------------------

# Suggested Architecture

Separate concerns such as:

-   UI
-   Backend API
-   GitHub connectors
-   AI services
-   Persistence
-   Background jobs
-   Authentication
-   Configuration

Repository connectors should be modular so supporting additional GitHub
Enterprise instances or entirely new systems requires minimal changes.

------------------------------------------------------------------------

# User Interface

Keep the first UI intentionally simple.

Home dashboard:

-   Overall engineering summary
-   Team attention items
-   Active milestones
-   Repository health

Repository page:

-   Current work
-   Risks
-   Recent activity
-   AI summary

Engineer page:

-   Current work
-   Completed work
-   Reviews
-   Blockers
-   AI-generated status update

------------------------------------------------------------------------

# Long-Term Ideas (Not MVP)

-   Slack integration
-   Daily standup generation
-   Weekly engineering reports
-   Release summaries
-   Cross-team dependency visualization
-   Executive dashboards
-   Natural-language querying ("What is Bob blocked on?")
-   Historical trend analysis
-   AI recommendations for workload balancing
-   Calendar-aware status summaries

------------------------------------------------------------------------

# How I'd Like You to Help

Act as a senior staff engineer and technical partner.

Help me:

-   Make good architectural decisions.
-   Avoid overengineering.
-   Build in small, valuable increments.
-   Explain important tradeoffs.
-   Refactor only when the code justifies it.
-   Maintain high code quality.
-   Keep the project moving toward a usable product.

If I start drifting into unnecessary complexity, challenge the approach
and recommend a smaller, working alternative.

Our goal is to produce a useful application as quickly as possible while
maintaining a solid foundation for future growth.
