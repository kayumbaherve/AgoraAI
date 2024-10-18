# app.py
from flask import Flask, request, redirect, render_template, jsonify
from square.client import Client
from models import db,  User, Inventory, Sales # Import the User model
from sqlalchemy.exc import IntegrityError
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from gpt import fetch_square_data, ask_openai_assistant
import requests
import json
import os
from dotenv import load_dotenv
import openai
from openai import OpenAI
import csv
from flask import send_from_directory, abort




openai.api_key = os.getenv("OPENAI_API_KEY")
load_dotenv()

# Load a configuration switch from an environment variable
public_access_enabled = os.getenv("PUBLIC_ACCESS_ENABLED", "false").lower() == "true"

app = Flask(__name__)

message_history =[]
user_id = 1

# Database configuration - Using SQLAlchemy for database interactions
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://admin:agoraadmin@db-agoratest.ctc86o2w47lh.us-west-2.rds.amazonaws.com:3306/db-agoratest'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

migrate = Migrate(app, db) ##updates the db to track changes over time 


# Initialize and create tables
with app.app_context():
    db.create_all()

##### Functions
def sync_items(user_id, square_json):
    try:
        # Extract image URLs
        image_urls = {img['id']: img['image_data']['url'] for img in square_json if img['type'] == 'IMAGE'}

        # Process items
        square_items = [item for item in square_json if item['type'] == "ITEM"]
        added_items = []
        modified_items = []
        removed_items = []
        
        # Existing database items
        db_items = Inventory.query.filter_by(user_id=user_id).all()
        db_item_sq_ids = {item.sq_id: item for item in db_items}

        for square_item in square_items:
            item_data = square_item.get('item_data', {})
            sq_id = square_item['id']
            sku = None
            quantity_on_hand = -3  # Default quantity

            # Find first variation with a SKU
            for variation in item_data.get('variations', []):
                if 'sku' in variation.get('item_variation_data', {}):
                    sku = variation['item_variation_data']['sku']
                    # Assuming the quantity is in the 'quantity_on_hand' field
                    quantity_on_hand = variation.get('item_variation_data', {}).get('quantity_on_hand', -4)
                    break

            item_image_url = None
            if item_data.get('image_ids'):
                first_image_id = item_data['image_ids'][0]
                item_image_url = image_urls.get(first_image_id)

            if sku and sq_id in db_item_sq_ids:
                # Update existing item
                db_item = db_item_sq_ids[sq_id]
                db_item.sku = sku
                db_item.image_url = item_image_url
                db_item.quantity_on_hand = quantity_on_hand  # Update quantity
                modified_items.append(db_item.id)
            elif sku:
                # Add new item
                new_item = Inventory(
                    user_id=user_id,
                    sq_id=sq_id,
                    sku=sku,
                    image_url=item_image_url,
                    quantity_on_hand=quantity_on_hand,  # Set quantity
                    # Set other fields or default them to 'N/A' or appropriate defaults
                )
                db.session.add(new_item)
                db.session.flush()
                added_items.append(new_item.id)

            db.session.commit()

        # Handle removed items
        square_sq_ids = [item['id'] for item in square_items]
        for db_item in db_items:
            if db_item.sq_id not in square_sq_ids:
                removed_items.append(db_item.id)
                db.session.delete(db_item)

        db.session.commit()

        return {
            'status': 'success',
            'added_items': added_items,
            'modified_items': modified_items,
            'removed_items': removed_items
        }
    except Exception as e:
        db.session.rollback()
        return {
            'status': 'error',
            'message': str(e)
        }



# Function to fetch user information based on user_id (basic version)
def get_user_info(user_id):
    # Replace this with your database query to fetch user information
    # For simplicity, we'll use a dictionary to represent user data
    users = {
        "user1": {
            "user_id": "user1",
            "store_name": "Sample Store 1",
            "store_description": "This is a sample store 1.",
        },
        "user2": {
            "user_id": "user2",
            "store_name": "Sample Store 2",
            "store_description": "This is a sample store 2.",
        },
        # Add more user entries as needed
    }

    # Check if the user_id exists in the dictionary
    user_data = users.get(user_id)

    if user_data:
        return f"User ID: {user_data['user_id']}\nStore Name: {user_data['store_name']}\nStore Description: {user_data['store_description']}"
    else:
        return "User not found"  # Handle the case when the user_id is not found in the database




# Function to interact with the OpenAI GPT model
def chat_with_model(user_id, user_input):
    global model

    # Fetch user information based on user_id (you can replace this with your own logic)
    user_info = get_user_info(user_id)

    # Append user information to the message history (if needed)
    message_history.append({"role": "user_info", "content": user_info})

    # Append the user's question to the message history
    message_history.append({"role": "user", "content": user_input})

    # Call OpenAI GPT model to generate a response
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = "gpt-4-1106-preview"  # Use the desired model
    messages = message_history

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        top_p=0.95
    )

    # Extract and return the response generated by the model
    gpt_response = response.choices[0].message.content
    return gpt_response

import requests

def fetch_square_images(access_token):
    """
    Fetches the catalog items along with image URLs from Square.

    :param access_token: Square access token for API authentication.
    :param location_id: Location ID for the Square account.
    :return: List of items with their image URLs.
    """
    headers = {
        'Square-Version': '2024-01-18',
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Endpoint to search catalog objects
    url = 'https://connect.squareup.com/v2/catalog/search'

    # Request body to search for items and include related objects (images)
    body = {
        "object_types": ["ITEM"],
        "include_related_objects": True
    }

    try:
        response = requests.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

        items_with_images = []
        for item in data.get('objects', []):
            # Check if item has image data
            if 'image_ids' in item['item_data'] and item['item_data']['image_ids']:
                first_image_id = item['item_data']['image_ids'][0]

                # Find the corresponding image object
                image_object = next((obj for obj in data.get('related_objects', []) if obj['id'] == first_image_id), None)
                if image_object:
                    items_with_images.append({
                        'name': item['item_data']['name'],
                        'image_url': image_object['image_data']['url']
                    })

        return items_with_images

    except requests.RequestException as e:
        return f"An error occurred: {e}"

# Example usage (Replace 'your_access_token' and 'your_location_id' with actual values)
access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"
items = fetch_square_images(access_token)
print(items)




def fetch_square_data_for_user(user_id):
    # Implement the logic to fetch and filter Square data for the given user ID
    # Dummy response for demonstration
    return [{"id": 1, "name": "Item 1", "price": 100}, {"id": 2, "name": "Item 2", "price": 150}]


# Dummy function for API token validation (replace with your actual logic)
def validate_api_token(user_id, api_token):
    # Implement your logic to validate the token
    # For now, let's assume it always returns True
    return True

###### /Functions





##### Routes


@app.route('/')
def index():
    return render_template('Agoraindex.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        # Use SQLAlchemy for querying the database
        user = User.query.filter_by(username=username).first()
        if user:
            return redirect('/Home')
        else:
            return "Invalid username or password."
    else:
        return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['Username']
        email = request.form['Email']
        #password = request.form['Password']

        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            return "User with the same username or email already exists."

        new_user = User(username=username, email=email)
        #new_user.set_password(password)  # Set the password hash
        db.session.add(new_user)
        db.session.commit()
        return redirect('/agoraindex')
    else:
        return render_template('signup.html')

@app.route('/main_page')
def main_page():
    return render_template('main_page.html')

@app.route('/agoraindex')
def agoraindex():
    return render_template('Agoraindex.html')

@app.route('/add_item/<int:user_id>/<int:id>/<string:sku>/<float:price>/<int:quantity>')
def add_item(user_id, id, sku, price, quantity):
    try:
        # Check if an item with the same SKU already exists for the specified user_id
        existing_item = Inventory.query.filter_by(user_id=user_id, sku=sku).first()
        if existing_item:
            return f"Item with SKU {sku} already exists for User ID {user_id}."

        # Create a new Inventory item with the provided values, including user_id and quantity_on_hand
        new_item = Inventory(
            user_id=user_id,
            id=id,
            sku=sku,
            list_price=price,
            quantity_on_hand=quantity,
            next_order_date=None,  # Use None instead of '-1' for datetime columns
            last_order_date=None,
            gtin='-1',
            vendor_code='-1',
            sq_id='-1'
        )
        # You may want to perform additional validation here

        db.session.add(new_item)
        db.session.commit()
        return f"Item with ID {id}, SKU {sku}, Price {price}, Quantity {quantity}, and User ID {user_id} added to the database!"
    except IntegrityError as e:
        # Handle the integrity error here
        return "Error: The provided user_id does not exist or there was a problem with the foreign key constraint."
#change

@app.route('/list_square_items/<int:user_id>', methods=['GET'])
def list_square_items_user(user_id):
    # Retrieve the API token from the request header (though it's not used for validation)
    api_token = request.headers.get('API-Token')

    # Continue with fetching Square items
    access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"
    url = "https://connect.squareup.com/v2/catalog/list"
    headers = {
        "Square-Version": "2022-04-20",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        square_items = json.loads(response.text)["objects"]
        # If user_id is 1, return the fetched items; otherwise, return an empty list or appropriate message
        if user_id == 1:
            return jsonify(square_items)
        else:
            return jsonify([])  # or return a message like "User not found" or "Invalid user ID"
    else:
        return jsonify({"error": f"Error: {response.status_code}"})


def validate_api_token(user_id, api_token):
    # Implement the logic to validate the API token for the given user_id
    # This function should return True if the token is valid, otherwise False
    # Example: return User.query.filter_by(id=user_id, api_token=api_token).first() is not None
    pass

def filter_square_items_for_user(square_items, user_id):
    # Implement the logic to filter square_items based on user_id
    # This could involve checking which items belong to the user in your database
    # Example: return [item for item in square_items if item_belongs_to_user(item, user_id)]
    pass

# Example use of the validate_api_token function
# Example: if not validate_api_token(user_id, api_token):
#             return jsonify({"error": "Invalid API token"}), 403



@app.route('/remove_item/<int:user_id>/<string:sku>')
def remove_item(user_id, sku):
    try:
        # Check if the item exists for the specified user_id and SKU
        item_to_remove = Inventory.query.filter_by(user_id=user_id, sku=sku).first()
        if not item_to_remove:
            return f"Item with SKU {sku} does not exist for User ID {user_id}."

        # Remove the item from the database
        db.session.delete(item_to_remove)
        db.session.commit()
        return f"Item with SKU {sku} removed for User ID {user_id}."
    except IntegrityError as e:
        # Handle any integrity error here
        return "Error: There was a problem removing the item."

@app.route('/remove_all_items/<int:user_id>')
def remove_all_items(user_id):
    try:
        # Remove all items for the specified user
        Inventory.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        return "All items for user {} have been removed.".format(user_id)
    except Exception as e:
        # Handle any exceptions or errors that may occur during the removal process
        return "An error occurred: {}".format(str(e))


@app.route('/modify_item/<int:user_id>/<string:sku>/<float:new_price>/<int:new_quantity>')
def modify_item(user_id, sku, new_price, new_quantity):
    try:
        # Check if the item exists for the specified user_id and SKU
        item_to_modify = Inventory.query.filter_by(user_id=user_id, sku=sku).first()
        if not item_to_modify:
            return f"Item with SKU {sku} does not exist for User ID {user_id}."

        # Update the item's price and quantity with the new values
        item_to_modify.list_price = new_price
        item_to_modify.quantity_on_hand = new_quantity

        db.session.commit()
        return f"Item with SKU {sku} modified for User ID {user_id}. New Price: {new_price}, New Quantity: {new_quantity}."
    except IntegrityError as e:
        # Handle any integrity error here
        return "Error: There was a problem modifying the item."

@app.route('/list_users')
def list_users():
    users = User.query.all()
    user_list = [user.username for user in users]
    return '\n'.join(user_list)

@app.route('/list_items/<int:user_id>', methods=['GET'])
def list_items(user_id):
    # Query the inventory items for the specified user_id
    items = Inventory.query.filter_by(user_id=user_id).all()

    # Render the HTML template with the items data
    return render_template('inventory_table.html', items=items)


@app.route('/query_inventory/<int:user_id>', methods=['GET'])
def query_inventory(user_id):
    try:
        # Query the inventory items for the specified user_id
        items = Inventory.query.filter_by(user_id=user_id).all()

        # Convert the items to a JSON-serializable format
        items_json = [{
            'Item ID': item.id,
            'SKU': item.sku,
            'Description': item.description,
            'List Price': item.list_price,
            'Sale Price': item.sale_price,
            'Quantity': item.quantity_on_hand,
            'Next Order Date': item.next_order_date,
            'Last Order Date': item.last_order_date,
            'GTIN': item.gtin,
            'Vendor Code': item.vendor_code,
            'Square ID': item.sq_id
        } for item in items]

        return jsonify(items_json)
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/list_square_items', methods=['GET'])
def list_square_items():
    access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"

    url = "https://connect.squareup.com/v2/catalog/list"
    headers = {
        "Square-Version": "2022-04-20",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        items = json.loads(response.text)["objects"]
        return render_template('square_inventory.html', items=items)
    else:
        return jsonify({"error": f"Error: {response.status_code}"})

# Add a route to log the JSON data to the browser console
@app.route('/list_square_items_json', methods=['GET'])
def list_square_items_json():
    access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"

    url = "https://connect.squareup.com/v2/catalog/list"
    headers = {
        "Square-Version": "2022-04-20",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        items = json.loads(response.text)["objects"]

        # Convert the JSON data to a string
        items_json = json.dumps(items, indent=4)

        # Create a JavaScript script that logs the JSON data to the browser console
        script = f'<script>console.log({items_json});</script>'

        # Return the script as a response to log the data in the browser console
        return script
    else:
        return jsonify({"error": f"Error: {response.status_code}"})
    


@app.route('/sync_items/<int:user_id>', methods=['GET'])
def sync_items_route(user_id):
    access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"

    url = "https://connect.squareup.com/v2/catalog/list"
    headers = {
        "Square-Version": "2022-04-20",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        square_json = response.json()["objects"]
        
        # Sync the inventory items with the Square data
        result = sync_items(user_id, square_json)  # Pass only user_id and square_json
        
        return jsonify(result)
    else:
        return jsonify({"error": f"Error fetching from Square API: {response.status_code}"})





@app.route('/process_speech', methods=['POST'])
def process_speech():
    user_input = request.form.get('finalTranscript')
    user_id = 1  # Assuming a fixed user ID for now

    # Fetch Square data
    square_data = fetch_square_data(user_id)

    # Ask the OpenAI assistant
    ai_response = ask_openai_assistant(user_input, square_data)

    # Return the AI's response
    return jsonify(response=ai_response)


@app.route('/ask_openai', methods=['POST'])
def openai_route():
    # Extract data from request
    question = request.json.get('question')
    user_id = 1  # or dynamically determine this 
    
    # Fetch data and get response
    square_data = fetch_square_data(user_id)
    response = ask_openai_assistant(question, square_data)
    return jsonify({"response": response})




# Route for the index page
@app.route('/nav')
def nav():
    return render_template('nav.html')

# Route for the index page
@app.route('/Home')
def Home():
    return render_template('home.html')
# Route for the index page
@app.route('/Why')
def Why():
    return render_template('why.html')
# Route for the index page
@app.route('/Store')
def Store():
    return render_template('store.html')
# Route for the index page
@app.route('/Contact')
def Contact():
    return render_template('contact.html')

@app.route('/submit-contact', methods=['POST'])
def submit_contact():
    name = request.form['name']
    email = request.form['email']
    message = request.form['message']

    file_exists = os.path.isfile('static/contact.csv')

    with open('static/contact.csv', mode='a', newline='') as file:
        writer = csv.writer(file)
        # If the file didn't exist, write a header
        if not file_exists:
            writer.writerow(['Name', 'Email', 'Message'])
        # Write the data row
        writer.writerow([name, email, message])

    return jsonify({'success': 'Your message has been recorded'})


@app.route('/square-inventory')
def square_inventory():
    access_token = "EAAAF4GpZvy6Od0YkAS9hM3jS-vIVOITp3WcdL1XWgB5FsUtKSGJNjmCd159xQgD"
    items = fetch_square_images(access_token)
    return render_template('square_inventory.html', items=items)






####Attempting to make it so chatGPT can access our code for edits.
@app.route('/public/<path:filename>')
def serve_public_file(filename):
    if not public_access_enabled:
        return jsonify({"error": "Public access is currently disabled."}), 403

    # Prevent directory traversal & validate filename
    if ".." in filename or filename.startswith("/"):
        return jsonify({"error": "Invalid file path."}), 400

    allowed_dirs = ['code', 'templates']
    allowed_base_dir = None
    for allowed_dir in allowed_dirs:
        if filename.startswith(f"{allowed_dir}/"):
            allowed_base_dir = os.path.join('public', allowed_dir)
            break

    if not allowed_base_dir:
        return jsonify({"error": "File not found."}), 404

    # Serve the file from the allowed directory
    try:
        # Adjust the filename to remove the directory prefix
        adjusted_filename = filename[len(allowed_dir)+1:]
        return send_from_directory(allowed_base_dir, adjusted_filename, as_attachment=True, mimetype='text/plain')
    except FileNotFoundError:
        return jsonify({"error": "File not found."}), 404



#### /Routes

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

