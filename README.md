# Special Agent

**⚠️ DEVELOPMENT BRANCH - EXPERIMENTAL ⚠️**

A smart Home Assistant custom component that uses Large Language Models (OpenAI & Claude) to understand natural language commands and control your smart home devices.

## Overview

Special Agent integrates advanced AI capabilities with Home Assistant, allowing you to use natural language to control your smart home devices. It interprets commands contextually, understands device locations, and executes appropriate actions.

### Key Features:

- **Natural Language Understanding**: Talk to your devices in everyday language
- **Context-Aware**: Understands device locations and relationships
- **Multi-Device Support**: Handles requests from multiple voice input devices simultaneously
- **Smart Device Control**: Controls lights, media players, climate devices, and more
- **Music Integration**: Searches and plays music through Spotify based on natural requests
- **Room-Aware Confirmations**: Asks for clear, room-specific confirmation before executing commands
- **Command History**: Maintains a detailed log of all commands, responses, and execution results
- **Seamless Integration**: Works with Home Assistant's conversation interface

## Installation

1. Copy this folder to your Home Assistant custom_components directory
2. Restart Home Assistant
3. Add the integration via Home Assistant's integration page
4. Configure with required API keys (OpenAI, Spotify)

## Usage

Once installed, you can interact with Special Agent through any Home Assistant conversation interface:

- "Turn on the lights in the kitchen"
- "Set the living room temperature to 72 degrees"
- "Play some jazz music in the bedroom"
- "Dim the lights in the office to 50%"

## Requirements

- Home Assistant (2023.8.0 or newer)
- OpenAI API key
- Spotify Premium account (optional, for music playback)

## Configuration

Configure through the Home Assistant UI:

1. Go to Configuration > Integrations
2. Add "Special Agent" integration
3. Enter your OpenAI API key
4. Optionally add Spotify credentials for music integration

## Command History

Special Agent maintains a detailed history of all user interactions in a JSON file located in the component directory:

- **What's recorded**: User requests, device IDs, responses, command execution status, timestamps
- **Purpose**: Troubleshooting, improving performance, understanding usage patterns
- **Location**: `command_history.json` in the component directory
- **Format**: Structured JSON that can be read with any text editor or parsed programmatically

The history is limited to the most recent 1000 interactions to manage file size.

## Privacy & Data Security

All voice processing happens locally in Home Assistant before being sent to external APIs. Only the text of your commands is sent to OpenAI/Claude for processing. No audio is recorded or transmitted.

The command history is stored locally on your Home Assistant server and is not sent to any external services.

## Support & Contribution

This is a development branch with experimental features. Issues and feature requests can be submitted through GitHub.