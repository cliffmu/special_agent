# Google Custom Search Setup Guide

## Prerequisites
You need:
1. A Google account
2. A Google Cloud project (free tier available)

## Step 1: Get Google API Key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable "Custom Search API"
   - Go to APIs & Services → Library
   - Search for "Custom Search API"
   - Click Enable
4. Create credentials
   - Go to APIs & Services → Credentials
   - Click "Create Credentials" → API Key
   - Copy your API key

## Step 2: Create Custom Search Engine

1. Go to [Programmable Search Engine](https://programmablesearchengine.google.com/)
2. Click "Add" to create new search engine
3. Configuration:
   - Search the entire web: **ON**
   - Sites to search: Leave empty (searches entire web)
   - Name: "Home Assistant Search" (or any name)
4. Click "Create"
5. Copy your Search Engine ID (cx)

## Step 3: Configure in Home Assistant

1. Go to Settings → Devices & Services
2. Find Special Agent
3. Click Configure
4. Enter:
   - Google Custom Search API Key: (from Step 1)
   - Google Custom Search Engine ID: (from Step 2)
5. Save

## Usage Limits

- Free tier: 100 searches/day
- Paid: $5 per 1000 queries (after free tier)

## Testing

After configuration, try:
- "What's the weather in New York?"
- "What was the Yankees score yesterday?"
- "Who won the latest presidential election?"

The agent will now use Google Search when it needs current information!
