import os
import sys
# DON'T CHANGE THIS !!!
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, send_from_directory, request, jsonify
from dotenv import load_dotenv
import vapi_python
from apify_client import ApifyClient
import anthropic
import math
import json

load_dotenv() # Load environment variables from .env file

# Initialize clients
vapi_api_key = os.getenv("VAPI_API_KEY")
apify_api_key = os.getenv("APIFY_API_KEY")
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

if not all([vapi_api_key, apify_api_key, anthropic_api_key]):
    print("Error: API keys for VAPI, Apify, or Anthropic are missing in .env file.")
    # In a real app, you might want to raise an error or exit

vapi = vapi_python.Vapi(api_key=vapi_api_key)
apify_client = ApifyClient(apify_api_key)
anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_default_secret_key_change_me')

# --- Helper Functions ---

def fetch_twitter_data(query, min_followers=1000000):
    """Fetches data from Twitter using Apify actor."""
    print(f"Fetching Twitter data for query: {query}")
    try:
        # Using quacker/twitter-scraper based on research
        actor_call = apify_client.actor("quacker/twitter-scraper").call(
            run_input={
                "searchTerms": [query],
                "searchMode": "live", # or 'top'
                "maxTweets": 50, # Limit tweets to keep processing manageable
                # Add other relevant filters based on FR3 if needed, e.g., min_faves, filter:verified
                # The requirement mentioned "top Twitter people with over 1M followers"
                # This specific actor might not directly filter by follower count during search,
                # but we can filter the results later or use a different actor if needed.
                # For now, focusing on getting tweets related to the query.
            }
        )
        # Fetch results from the dataset
        dataset_items = apify_client.dataset(actor_call['defaultDatasetId']).list_items().items
        print(f"Fetched {len(dataset_items)} items from Apify.")
        return dataset_items
    except Exception as e:
        print(f"Error calling Apify: {e}")
        return None

def aggregate_tweets(items):
    """Aggregates tweet text from Apify results."""
    if not items:
        return ""
    # Extract text, remove duplicates (based on tweet text itself for simplicity)
    texts = list(set([item.get('text', '') for item in items if item.get('text')]))
    aggregated = "\n\n".join(texts)
    print(f"Aggregated text length: {len(aggregated)} chars")
    return aggregated

def summarize_with_anthropic(text, domain, time_limit_minutes):
    """Summarizes text using Anthropic API."""
    if not text:
        return "No content available to summarize.", 0

    print(f"Sending text to Anthropic for summarization (domain: {domain}, time: {time_limit_minutes}m)")
    max_words = time_limit_minutes * 150 # Target words based on 150 wpm

    try:
        # Constructing a prompt for Anthropic
        prompt = f"Summarize the following text about '{domain}'. The summary should be concise enough to be spoken aloud in approximately {time_limit_minutes} minute(s) (around {max_words} words). Focus on the key information and main points.\n\nText:\n{text}"

        message = anthropic_client.messages.create(
            model="claude-3-opus-20240229", # Or another suitable model
            max_tokens=max_words + 100, # Allow some buffer
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        summary = message.content[0].text
        estimated_words = len(summary.split())
        estimated_speech_time_sec = (estimated_words / 150) * 60
        print(f"Anthropic summary received: {estimated_words} words, est. {estimated_speech_time_sec:.1f}s")
        return summary, estimated_speech_time_sec

    except Exception as e:
        print(f"Error calling Anthropic: {e}")
        return None, 0

# --- Flask Routes ---

@app.route('/api/process-brief', methods=['POST'])
def process_brief():
    data = request.json
    time_budget_str = data.get('time_budget')
    domain_query = data.get('domain_query')

    if not time_budget_str or not domain_query:
        return jsonify({"error": "Missing time_budget or domain_query"}), 400

    try:
        time_budget_minutes = int(time_budget_str.split()[0])
        if time_budget_minutes <= 0:
            raise ValueError("Time budget must be positive")
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid time_budget format. Use 'X minutes' where X is a positive number."}), 400

    print(f"Received request: Time Budget={time_budget_minutes} mins, Query='{domain_query}'")

    # 1. Fetch Data from Apify (FR3)
    twitter_data = fetch_twitter_data(domain_query)
    if twitter_data is None:
        # Error already logged in helper function
        return jsonify({"error": "Failed to fetch data from Twitter source."}), 500
    if not twitter_data:
        # FR8: No data found
        # In a real VAPI integration, this text would be sent to vapi.speak()
        return jsonify({"summary": f"Sorry, I couldn’t find anything on '{domain_query}'."}), 200

    # 2. Aggregate Results (FR4)
    aggregated_text = aggregate_tweets(twitter_data)
    if not aggregated_text:
        return jsonify({"summary": f"Sorry, I found some items for '{domain_query}' but couldn't extract meaningful text to summarize."}), 200

    # 3. Summarize with Anthropic (FR5)
    summary_text, estimated_time_sec = summarize_with_anthropic(aggregated_text, domain_query, time_budget_minutes)
    if summary_text is None:
        # Error logged in helper
        # FR8: API failure fallback (could also retry)
        return jsonify({"summary": "Sorry, I encountered an error while trying to summarize the content."}), 500

    # 4. Handle Response Length & Fallbacks (FR6)
    max_allowed_time_sec = time_budget_minutes * 60
    if estimated_time_sec > max_allowed_time_sec * 1.1: # Allow 10% buffer
        print(f"Summary too long ({estimated_time_sec:.1f}s > {max_allowed_time_sec}s). Generating fallback.")
        # FR6 Fallback: Generate a shorter bullet list (simple version for now)
        # A more robust fallback might involve asking Anthropic for bullet points
        # or simply truncating, but bullets are requested.
        bullets = "\n".join([f"- {line.strip()}" for line in summary_text.split('.')[:3] if line.strip()])
        summary_text = f"The full summary might take too long to read. Here are the highlights:\n{bullets}"

    # 5. Send summary to VAPI TTS (FR7) - Placeholder
    # In a real scenario, this summary would be sent to VAPI's speak() endpoint.
    # This might be done by the client-side JS after receiving the summary,
    # or potentially via a VAPI API call from the backend if needed.
    # For now, we just return the summary.

    return jsonify({
        "status": "success",
        "summary": summary_text,
        "original_query": domain_query,
        "time_budget_minutes": time_budget_minutes
    })

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_folder_path = app.static_folder
    if static_folder_path is None:
            return "Static folder not configured", 404

    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        index_path = os.path.join(static_folder_path, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(static_folder_path, 'index.html')
        else:
            # Serve a basic placeholder HTML if index.html doesn't exist
            # We will create a proper index.html later for the UI button and VAPI JS
            return """
            <html>
                <head><title>Voice Brief</title></head>
                <body>
                    <h1>Voice Brief App - Backend Ready</h1>
                    <p>Core backend logic for fetching, aggregating, and summarizing is implemented.</p>
                    <p>Next steps involve creating the frontend UI (index.html with JavaScript) to integrate with VAPI for voice input/output and trigger the backend API.</p>
                    <p>API endpoint: /api/process-brief (POST)</p>
                    <p>Example POST body: {"time_budget": "5 minutes", "domain_query": "stock market"}</p>
                </body>
            </html>
            """, 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False) # Disable debug for deployment readiness

