
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
# from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
from flask_migrate import Migrate
import os
import pika
import json
import datetime
import requests
import uuid
import jwt
from werkzeug.exceptions import BadRequest, Unauthorized, NotFound

app = Flask(__name__)
CORS(app)


import logging
import sys
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record, self.datefmt),
        })

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
handler.setLevel(logging.INFO)

app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)

file_handler = logging.FileHandler('/var/log/payment.log')
file_handler.setFormatter(JsonFormatter())
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)

# Configuration
# app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@payment-db:5432/payment_db')
uri = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/payment_db')
if uri.startswith('postgres://'):
    uri = uri.replace('postgres://', 'postgresql://', 1)

app.logger.info(f"Connecting to database at {uri}")

app.config['SQLALCHEMY_DATABASE_URI'] = uri

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your_secret_key')

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
# jwt = JWTManager(app)

# RabbitMQ connection
def get_rabbitmq_connection():
    rabbitmq_url = os.environ.get('MESSAGE_QUEUE_URL', 'amqp://guest:guest@rabbitmq:5672')
    connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
    app.logger.info(f"Connected to RabbitMQ at {rabbitmq_url}")
    return connection

# Service discovery
ORDER_SERVICE_URL = os.environ.get('ORDER_SERVICE_URL', 'http://order-service:5000/order-api')


# Models
class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    status = db.Column(db.String(20), default='pending')
    payment_method_type = db.Column(db.String(20), nullable=False)
    payment_method_last_four = db.Column(db.String(4))
    transaction_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class PaymentMethod(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    last_four = db.Column(db.String(4))
    expiry_date = db.Column(db.String(7))
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()

def get_user_from_token():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        app.logger.error('Authorization header is missing or invalid')
        return None
    
    token = auth_header.split(' ')[1]
    
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])

        return str(payload.get('user_id'))
    except:
        return None

def token_required(f):
    def decorator(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        
        if not token:
            app.logger.error('Token is missing')
            raise Unauthorized('Token is missing')
        
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
            if not payload.get('user_id'):
                app.logger.error('Invalid token payload')
                raise Unauthorized('Invalid token')
            
            # Check if user is staff
            # if not payload.get('is_staff', False):
            #     raise Unauthorized('Admin access required')
                
        except jwt.ExpiredSignatureError:
            app.logger.error('Token has expired')
            raise Unauthorized('Token has expired')
        except jwt.InvalidTokenError:
            app.logger.error('Invalid token')
            raise Unauthorized('Invalid token')
        except Exception as e:
            app.logger.error(f"Token validation error: {str(e)}")
            raise Unauthorized('Token validation error')
        
        return f(*args, **kwargs)
    
    decorator.__name__ = f.__name__
    return decorator


# Health check endpoint
@app.route('/payment-api/health', methods=['GET'])
def health_check():
    app.logger.info('Health check endpoint called')
    return jsonify({'status': 'healthy', 'service': 'order-service'}), 200

# Endpoints
@app.route('/payment-api/payments', methods=['POST'])
@token_required
def process_payment():
    # user_id = get_jwt_identity()
    user_id = get_user_from_token()
    data = request.json
    
    order_id = data.get('orderId')
    amount = data.get('amount')
    currency = data.get('currency', 'USD')
    payment_method_id = data.get('paymentMethodId')
    
    # Validate input
    if not order_id or not amount:
        app.logger.error('Order ID and amount are required')
        return jsonify({'error': 'Order ID and amount are required'}), 400
    
    # Get payment method if provided
    payment_method = None
    if payment_method_id:
        payment_method = PaymentMethod.query.filter_by(id=payment_method_id, user_id=user_id).first()
        if not payment_method:
            app.logger.error('Payment method not found')
            return jsonify({'error': 'Payment method not found'}), 404
    else:
        # Use default payment method
        payment_method = PaymentMethod.query.filter_by(user_id=user_id, is_default=True).first()
        if not payment_method:
            app.logger.error('No default payment method found')
            return jsonify({'error': 'No default payment method found'}), 400
    
    # In a real system, we would process the payment with a payment processor
    # For this example, we'll simulate a successful payment
    transaction_id = str(uuid.uuid4())
    payment_status = 'completed'
    
    # Create payment record
    payment = Payment(
        order_id=order_id,
        user_id=user_id,
        amount=amount,
        currency=currency,
        status=payment_status,
        payment_method_type=payment_method.type,
        payment_method_last_four=payment_method.last_four,
        transaction_id=transaction_id
    )
    
    db.session.add(payment)
    db.session.commit()
    app.logger.info(f"Payment processed: {payment.id}")
    
    # Update order payment status
    try:
        response = requests.put(
            f"{ORDER_SERVICE_URL}/internal/orders/{order_id}/status",
            json={'paymentStatus': payment_status}
        )
        if not response.ok:
            app.logger.error(f"Failed to update order status: {response.text}")
        else:
            app.logger.info(f"Order status updated: {response.json()}")
    except Exception as e:
        app.logger.error(f"Failed to update order status: {str(e)}")
    
    # Publish payment event to RabbitMQ
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        channel.exchange_declare(exchange='payment_events', exchange_type='topic', durable=True)
        
        message = {
            'event': 'payment.completed',
            'data': {
                'paymentId': payment.id,
                'orderId': order_id,
                'userId': user_id,
                'amount': amount,
                'status': payment_status,
                'transactionId': transaction_id
            }
        }
        
        channel.basic_publish(
            exchange='payment_events',
            routing_key='payment.completed',
            body=json.dumps(message)
        )
        
        connection.close()
    except Exception as e:
        app.logger.error(f"Failed to publish payment event: {str(e)}")
    
    # Return payment details
    return jsonify({
        'id': payment.id,
        'orderId': payment.order_id,
        'amount': payment.amount,
        'currency': payment.currency,
        'status': payment.status,
        'paymentMethod': {
            'type': payment.payment_method_type,
            'lastFour': payment.payment_method_last_four
        },
        'transactionId': payment.transaction_id,
        'createdAt': payment.created_at.isoformat()
    }), 201

@app.route('/payment-api/payments/order/<int:order_id>', methods=['GET'])
@token_required
def get_payment_status(order_id):
    # user_id = get_jwt_identity()
    user_id = get_user_from_token()
    
    payment = Payment.query.filter_by(order_id=order_id).order_by(Payment.created_at.desc()).first()
    app.logger.info(f"Payment status requested for order ID: {order_id}")
    
    if not payment:
        app.logger.error('Payment not found')
        return jsonify({'error': 'Payment not found'}), 404
    
    # Check if user is authorized
    if payment.user_id != user_id:
        app.logger.error('Unauthorized access to payment details')
        return jsonify({'error': 'Unauthorized access'}), 403
    
    return jsonify({
        'id': payment.id,
        'orderId': payment.order_id,
        'amount': payment.amount,
        'currency': payment.currency,
        'status': payment.status,
        'paymentMethod': {
            'type': payment.payment_method_type,
            'lastFour': payment.payment_method_last_four
        },
        'transactionId': payment.transaction_id,
        'createdAt': payment.created_at.isoformat()
    })

@app.route('/payment-api/payment-methods', methods=['GET'])
@token_required
def get_payment_methods():
    # user_id = get_jwt_identity()
    user_id = get_user_from_token()
    
    payment_methods = PaymentMethod.query.filter_by(user_id=user_id).all()
    app.logger.info(f"Payment methods requested for user ID: {user_id}")
    
    result = []
    for method in payment_methods:
        result.append({
            'id': method.id,
            'type': method.type,
            'lastFour': method.last_four,
            'expiryDate': method.expiry_date,
            'isDefault': method.is_default
        })
    
    return jsonify(result)

@app.route('/payment-api/payment-methods', methods=['POST'])
@token_required
def add_payment_method():
    # user_id = get_jwt_identity()
    user_id = get_user_from_token()

    data = request.json
    
    # Validate input
    if not data.get('type'):
        app.logger.error('Payment method type is required')
        return jsonify({'error': 'Payment method type is required'}), 400
    
    # For credit cards, additional validation
    if data.get('type') == 'credit_card':
        if not data.get('lastFour') or not data.get('expiryDate'):
            app.logger.error('Last four digits and expiry date are required for credit cards')
            return jsonify({'error': 'Last four digits and expiry date are required for credit cards'}), 400
    
    # Check if this should be the default
    is_default = data.get('isDefault', False)
    
    # If this is being set as default, unset any existing default
    if is_default:
        PaymentMethod.query.filter_by(user_id=user_id, is_default=True).update({'is_default': False})
    
    # Create new payment method
    payment_method = PaymentMethod(
        user_id=user_id,
        type=data.get('type'),
        last_four=data.get('lastFour'),
        expiry_date=data.get('expiryDate'),
        is_default=is_default
    )
    
    db.session.add(payment_method)
    db.session.commit()
    app.logger.info(f"Payment method added for user ID: {user_id}")
    
    return jsonify({
        'id': payment_method.id,
        'type': payment_method.type,
        'lastFour': payment_method.last_four,
        'expiryDate': payment_method.expiry_date,
        'isDefault': payment_method.is_default
    }), 201

@app.route('/payment-api/payments/<int:payment_id>/refund', methods=['POST'])
@token_required
def request_refund(payment_id):
    # user_id = get_jwt_identity()
    user_id = get_user_from_token()
    data = request.json
    
    payment = Payment.query.filter_by(id=payment_id).first_or_404()
    app.logger.info(f"Refund requested for payment ID: {payment_id}")
    
    # Check if user is authorized
    if payment.user_id != user_id:
        app.logger.error('Unauthorized access to payment details')
        return jsonify({'error': 'Unauthorized access'}), 403
    
    # Check if payment can be refunded
    if payment.status != 'completed':
        app.logger.error('Only completed payments can be refunded')
        return jsonify({'error': 'Only completed payments can be refunded'}), 400
    
    # Get refund amount (default to full amount if not specified)
    refund_amount = data.get('amount', payment.amount)
    
    # In a real system, we would process the refund with a payment processor
    # For this example, we'll simulate a successful refund
    payment.status = 'refunded'
    db.session.commit()
    app.logger.info(f"Payment refunded: {payment.id}")
    
    # Publish refund event to RabbitMQ
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        channel.exchange_declare(exchange='payment_events', exchange_type='topic', durable=True)
        
        message = {
            'event': 'payment.refunded',
            'data': {
                'paymentId': payment.id,
                'orderId': payment.order_id,
                'userId': user_id,
                'amount': refund_amount
            }
        }
        
        channel.basic_publish(
            exchange='payment_events',
            routing_key='payment.refunded',
            body=json.dumps(message)
        )
        
        connection.close()
    except Exception as e:
        app.logger.error(f"Failed to publish refund event: {str(e)}")
    
    # Return updated payment details
    return jsonify({
        'id': payment.id,
        'orderId': payment.order_id,
        'amount': payment.amount,
        'currency': payment.currency,
        'status': payment.status,
        'paymentMethod': {
            'type': payment.payment_method_type,
            'lastFour': payment.payment_method_last_four
        },
        'transactionId': payment.transaction_id,
        'createdAt': payment.created_at.isoformat()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
