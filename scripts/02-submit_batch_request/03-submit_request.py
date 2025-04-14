import os
import openai

openai.api_key = os.environ["OPENAI_API_KEY"]
client = openai.OpenAI()

# Upload the JSONL file for batch processing
batch_input_file = client.files.create(
    file=open("./processed/batch_requests/chatgpt_batch.jsonl", "rb"),
    purpose="batch"
)

# Print to console (optional)
print(f"Batch request: {batch_input_file.id}")

# Write the response to a txt file
with open("./processed/batch_requests/batch_file_id.txt", "w") as f:
    f.write(str(batch_input_file.id))

# create the batch
batch = client.batches.create(
    input_file_id=batch_input_file.id,
    endpoint="/v1/chat/completions",
    completion_window="24h",
    metadata={
        "description": "extract grade from patholoy report"
    }
)

# Write the response to a txt file
with open("./processed/batch_requests/batch_request_id.txt", "w") as f:
    f.write(str(batch.id))

# load the batch id and check batch status ------------------------------
with open('./processed/batch_requests/batch_request_id.txt', 'r') as file:
    batch_id = file.read()

# check the batch status
const batch = client.batches.retrieve(batch_id)
print(batch)

# load the file id and check results ------------------------------

with open('./processed/batch_requests/batch_file_id.txt', 'r') as file:
    file_id = file.read()

file_response = client.files.content(file_id)
print(file_response.text)