@echo off
REM Figma MCP Setup Script for Windows
REM Sets up Figma MCP integration with environment variables

setlocal EnableDelayedExpansion

echo ========================================
echo     Figma MCP Integration Setup
echo ========================================
echo.

echo This script will help you configure Figma MCP integration.
echo.

REM Check if .env file exists
if not exist ".env" (
    echo Creating .env file...
    echo # Figma MCP Integration > .env
    echo FIGMA_API_KEY= >> .env
    echo.
)

echo Please choose your setup option:
echo.
echo 1. Official Figma Dev Mode MCP Server (Recommended)
echo    - Requires Figma Desktop app
echo    - Works with Dev/Full seats on Professional+ plans
echo    - Real-time design context extraction
echo.
echo 2. Community Figma MCP Server
echo    - Requires Figma API key
echo    - Works with any Figma plan
echo    - File access and commenting features
echo.
echo 3. Both (Maximum functionality)
echo.

set /p choice="Enter your choice (1, 2, or 3): "

if "%choice%"=="1" goto :official_only
if "%choice%"=="2" goto :community_only
if "%choice%"=="3" goto :both_servers
goto :invalid_choice

:official_only
echo.
echo ========================================
echo    Official Figma Dev Mode MCP Setup
echo ========================================
echo.
echo To complete setup:
echo 1. Download Figma Desktop from https://figma.com/downloads
echo 2. Open Figma Desktop
echo 3. Go to Figma Menu → Preferences
echo 4. Enable "Dev Mode MCP Server"
echo 5. Restart Cursor
echo.
echo The server will run at: http://127.0.0.1:3845/sse
echo Configuration is already in .cursor/mcp.json
echo.
goto :finish

:community_only
echo.
echo ========================================
echo    Community Figma MCP Server Setup
echo ========================================
echo.
goto :setup_api_key

:both_servers
echo.
echo ========================================
echo    Setting Up Both Figma MCP Servers
echo ========================================
echo.
echo For Official Dev Mode Server:
echo 1. Download Figma Desktop from https://figma.com/downloads
echo 2. Open Figma Desktop
echo 3. Go to Figma Menu → Preferences
echo 4. Enable "Dev Mode MCP Server"
echo.
echo For Community Server, you'll need an API key:
echo.
goto :setup_api_key

:setup_api_key
echo To get your Figma API key:
echo 1. Go to https://figma.com
echo 2. Click your profile → Settings
echo 3. Go to Security tab
echo 4. Generate Personal Access Token
echo 5. Grant scopes: "File content" and "Comments"
echo.

set /p api_key="Enter your Figma API key (or press Enter to skip): "

if "%api_key%"=="" (
    echo.
    echo Skipping API key setup. You can add it later with:
    echo scripts-tools\env_manager.bat set FIGMA_API_KEY "your_api_key_here"
    echo.
) else (
    echo.
    echo Setting up Figma API key...
    call scripts-tools\env_manager.bat set FIGMA_API_KEY "%api_key%"
    if !errorlevel! equ 0 (
        echo ✓ Figma API key set successfully!
    ) else (
        echo ✗ Failed to set API key. Please run manually:
        echo scripts-tools\env_manager.bat set FIGMA_API_KEY "%api_key%"
    )
    echo.
)

goto :finish

:invalid_choice
echo.
echo Invalid choice. Please run the script again and choose 1, 2, or 3.
echo.
goto :end

:finish
echo ========================================
echo           Setup Complete!
echo ========================================
echo.
echo Your Figma MCP integration is configured.
echo.
echo Next steps:
echo 1. Restart Cursor to load the new MCP configuration
echo 2. Test with a Figma design file
echo 3. Read the full guide: docs\chats\FIGMA_MCP_SETUP.md
echo.
echo Example usage:
echo - "What's in my current Figma selection?"
echo - "Analyze this Figma file: [URL]"
echo - "Generate React components from this design"
echo.

:end
pause 