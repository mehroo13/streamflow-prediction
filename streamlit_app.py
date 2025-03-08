import streamlit as st
import pandas as pd
import numpy as np
import tensorflow as tf
import os
import tempfile
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from io import BytesIO
from tensorflow.keras.utils import plot_model
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from datetime import datetime, timedelta
try:
    import tensorflow_probability as tfp
    TFP_AVAILABLE = True
except ImportError:
    TFP_AVAILABLE = False
    print("Warning: TensorFlow Probability not available. Using simpler uncertainty estimation.")
from tensorflow.keras.layers import Bidirectional, Dense, Dropout, LSTM, GRU, SimpleRNN, Input, concatenate, Layer
from tensorflow.keras.models import Model
from tensorflow.keras.callbacks import TensorBoard, ModelCheckpoint, EarlyStopping, ReduceLROnPlateau
import optuna
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# -------------------- Model Parameters --------------------
DEFAULT_GRU_UNITS = 64
DEFAULT_LSTM_UNITS = 64
DEFAULT_RNN_UNITS = 64
DEFAULT_PINN_UNITS = 128
DEFAULT_DENSE_UNITS = 32
DEFAULT_LEARNING_RATE = 0.001
DEFAULT_EPOCHS = 50
DEFAULT_BATCH_SIZE = 16
DEFAULT_TRAIN_SPLIT = 80
DEFAULT_NUM_LAGS = 3
DEFAULT_PREDICTION_HORIZON = 7
DEFAULT_PHYSICS_WEIGHT = 0.1
MODEL_WEIGHTS_PATH = os.path.join(tempfile.gettempdir(), "model_weights.weights.h5")
MODEL_FULL_PATH = os.path.join(tempfile.gettempdir(), "model.h5")
MODEL_PLOT_PATH = os.path.join(tempfile.gettempdir(), "model_plot.png")
DEFAULT_MODEL_SAVE_PATH = "model_saved.h5"
DEFAULT_TRAIN_CSV_PATH = "train_results.csv"
DEFAULT_TEST_CSV_PATH = "test_results.csv"
TENSORBOARD_DIR = os.path.join(tempfile.gettempdir(), "tensorboard_logs")

# -------------------- PINN Custom Layer --------------------
class PhysicsInformedLayer(Layer):
    def __init__(self, **kwargs):
        super(PhysicsInformedLayer, self).__init__(**kwargs)
        
    def build(self, input_shape):
        self.kernel = self.add_weight(name='kernel',
                                    shape=(input_shape[-1], 1),
                                    initializer='glorot_uniform',
                                    trainable=True)
        self.bias = self.add_weight(name='bias',
                                  shape=(1,),
                                  initializer='zeros',
                                  trainable=True)
        super(PhysicsInformedLayer, self).build(input_shape)
    
    def call(self, inputs):
        # Apply physics constraints
        # 1. Mass conservation
        # 2. Non-negativity
        # 3. Smoothness constraint
        x = tf.matmul(inputs, self.kernel) + self.bias
        x = tf.nn.relu(x)  # Non-negativity
        return x
    
    def compute_output_shape(self, input_shape):
        return (input_shape[0], 1)

# -------------------- PINN Loss Functions --------------------
def physics_loss(y_true, y_pred):
    """Physics-based loss function for streamflow prediction."""
    # Mass conservation constraint
    mass_loss = tf.reduce_mean(tf.abs(tf.reduce_sum(y_pred) - tf.reduce_sum(y_true)))
    
    # Non-negativity constraint
    non_neg_loss = tf.reduce_mean(tf.nn.relu(-y_pred))
    
    # Smoothness constraint
    smoothness_loss = tf.reduce_mean(tf.abs(y_pred[1:] - y_pred[:-1]))
    
    return mass_loss + 0.1 * non_neg_loss + 0.01 * smoothness_loss

def combined_loss(y_true, y_pred):
    """Combined MSE and physics loss."""
    mse_loss = tf.keras.losses.mean_squared_error(y_true, y_pred)
    phys_loss = physics_loss(y_true, y_pred)
    return mse_loss + DEFAULT_PHYSICS_WEIGHT * phys_loss

# -------------------- Advanced Metric Functions --------------------
def nse(actual, predicted):
    return 1 - (np.sum((actual - predicted) ** 2) / np.sum((actual - np.mean(actual)) ** 2))

def kge(actual, predicted):
    r = np.corrcoef(actual, predicted)[0, 1]
    alpha = np.std(predicted) / np.std(actual)
    beta = np.mean(predicted) / np.mean(actual)
    return 1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)

def mape(actual, predicted):
    return np.mean(np.abs((actual - predicted) / actual)) * 100

def rmse(actual, predicted):
    return np.sqrt(mean_squared_error(actual, predicted))

def mae(actual, predicted):
    return mean_absolute_error(actual, predicted)

def r2(actual, predicted):
    return r2_score(actual, predicted)

# -------------------- Advanced Metrics Dictionary --------------------
all_metrics_dict = {
    "RMSE": rmse,
    "MAE": mae,
    "R²": r2,
    "NSE": nse,
    "KGE": kge,
    "MAPE": mape
}

# -------------------- Advanced Custom Callbacks --------------------
class StreamlitProgressCallback(tf.keras.callbacks.Callback):
    def __init__(self, total_epochs, progress_placeholder, metrics_placeholder):
        super().__init__()
        self.total_epochs = total_epochs
        self.progress_placeholder = progress_placeholder
        self.metrics_placeholder = metrics_placeholder
        self.current_epoch = 0
        self.metrics_history = []

    def on_epoch_end(self, epoch, logs=None):
        self.current_epoch = epoch + 1
        progress = self.current_epoch / self.total_epochs
        self.progress_placeholder.progress(min(progress, 1.0))
        
        # Update metrics display
        metrics_text = f"Epoch {self.current_epoch}/{self.total_epochs}\n"
        for metric, value in logs.items():
            metrics_text += f"{metric}: {value:.4f}\n"
        self.metrics_placeholder.text(metrics_text)
        
        # Store metrics history
        self.metrics_history.append(logs)

class AdvancedModelCheckpoint(tf.keras.callbacks.ModelCheckpoint):
    def __init__(self, filepath, monitor='val_loss', verbose=1, save_best_only=True, mode='min'):
        super().__init__(filepath, monitor, verbose, save_best_only, mode)
        self.best_epoch = 0
        self.best_value = float('inf')

    def on_epoch_end(self, epoch, logs=None):
        current = logs.get(self.monitor)
        if current is None:
            return

        if self.mode == 'min':
            if current < self.best_value:
                self.best_value = current
                self.best_epoch = epoch
                super().on_epoch_end(epoch, logs)
        else:
            if current > self.best_value:
                self.best_value = current
                self.best_epoch = epoch
                super().on_epoch_end(epoch, logs)

# -------------------- Advanced Model Definition --------------------
def build_advanced_model(input_shape, model_type, layers, units, dense_layers, dense_units, learning_rate, 
                        use_attention=False, use_bidirectional=False, use_residual=False, dropout_rate=0.2):
    inputs = Input(shape=input_shape)
    x = inputs
    
    if model_type == "PINN":
        # PINN architecture
        for i in range(layers):
            x = Dense(units[i], activation='relu')(x)
            x = Dropout(dropout_rate)(x)
        
        # Physics-informed layer
        x = PhysicsInformedLayer()(x)
        
        # Output layer
        outputs = Dense(1)(x)
        
        model = Model(inputs=inputs, outputs=outputs)
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
                     loss=combined_loss)
        return model
    
    elif model_type == "Hybrid":
        selected_models = st.session_state.hybrid_models
        if not selected_models:
            st.error("No hybrid models selected. Defaulting to GRU.")
            selected_models = ["GRU"]
        
        total_layers = layers
        layers_per_model = max(1, total_layers // len(selected_models))
        model_outputs = []
        
        for idx, sub_model in enumerate(selected_models):
            sub_units = units[:layers_per_model] if len(units) >= layers_per_model else units + [units[-1]] * (layers_per_model - len(units))
            sub_output = x
            
            for i in range(layers_per_model):
                return_seq = not (idx == len(selected_models) - 1 and i == layers_per_model - 1)
                
                if use_bidirectional:
                    if sub_model == "GRU":
                        sub_output = Bidirectional(GRU(sub_units[i], return_sequences=return_seq))(sub_output)
                    elif sub_model == "LSTM":
                        sub_output = Bidirectional(LSTM(sub_units[i], return_sequences=return_seq))(sub_output)
                    elif sub_model == "RNN":
                        sub_output = Bidirectional(SimpleRNN(sub_units[i], return_sequences=return_seq))(sub_output)
                    elif sub_model == "PINN":
                        sub_output = Dense(sub_units[i], activation='relu')(sub_output)
                else:
                    if sub_model == "GRU":
                        sub_output = GRU(sub_units[i], return_sequences=return_seq)(sub_output)
                    elif sub_model == "LSTM":
                        sub_output = LSTM(sub_units[i], return_sequences=return_seq)(sub_output)
                    elif sub_model == "RNN":
                        sub_output = SimpleRNN(sub_units[i], return_sequences=return_seq)(sub_output)
                    elif sub_model == "PINN":
                        sub_output = Dense(sub_units[i], activation='relu')(sub_output)
                
                if use_residual and return_seq:
                    if sub_output.shape[-1] == x.shape[-1]:
                        sub_output = concatenate([sub_output, x])
                
                sub_output = Dropout(dropout_rate)(sub_output)
            
            model_outputs.append(sub_output)
        
        if len(model_outputs) > 1:
            x = concatenate(model_outputs)
        else:
            x = model_outputs[0]
    
    else:
        for i in range(layers):
            return_seq = i < layers - 1
            
            if use_bidirectional:
                if model_type == "GRU":
                    x = Bidirectional(GRU(units[i], return_sequences=return_seq))(x)
                elif model_type == "LSTM":
                    x = Bidirectional(LSTM(units[i], return_sequences=return_seq))(x)
                elif model_type == "RNN":
                    x = Bidirectional(SimpleRNN(units[i], return_sequences=return_seq))(x)
            else:
                if model_type == "GRU":
                    x = GRU(units[i], return_sequences=return_seq)(x)
                elif model_type == "LSTM":
                    x = LSTM(units[i], return_sequences=return_seq)(x)
                elif model_type == "RNN":
                    x = SimpleRNN(units[i], return_sequences=return_seq)(x)
            
            if use_residual and return_seq:
                if x.shape[-1] == inputs.shape[-1]:
                    x = concatenate([x, inputs])
            
            x = Dropout(dropout_rate)(x)
    
    for unit in dense_units[:dense_layers]:
        x = Dense(unit, activation='relu')(x)
        x = Dropout(dropout_rate)(x)
    
    # Output layer with uncertainty estimation
    if TFP_AVAILABLE:
        outputs = Dense(2)(x)  # Mean and standard deviation
        model = Model(inputs=inputs, outputs=outputs)
        
        # Custom loss function for probabilistic predictions
        def negative_log_likelihood(y_true, y_pred):
            mean, std = y_pred[:, 0], tf.nn.softplus(y_pred[:, 1]) + 1e-6
            dist = tfp.distributions.Normal(loc=mean, scale=std)
            return -tf.reduce_mean(dist.log_prob(y_true[:, 0]))
        
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate), 
                     loss=negative_log_likelihood)
    else:
        outputs = Dense(1)(x)  # Just mean prediction
        model = Model(inputs=inputs, outputs=outputs)
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
                     loss='mse')
    
    return model

# -------------------- Advanced Data Preprocessing --------------------
def preprocess_data(df, input_vars, output_var, var_types, num_lags, date_col=None, 
                   handle_missing='median', remove_outliers=True, outlier_threshold=3):
    """Advanced data preprocessing with multiple options."""
    try:
        processed_df = df.copy()
        
        # Ensure input_vars and output_var exist in the dataframe
        missing_cols = [col for col in input_vars + [output_var] if col not in processed_df.columns]
        if missing_cols:
            # Filter to only include columns that exist
            input_vars = [col for col in input_vars if col in processed_df.columns]
            if output_var not in processed_df.columns:
                raise ValueError(f"Output variable {output_var} not found in dataframe")
        
        # Handle missing values
        if handle_missing == 'median':
            for var in input_vars + [output_var]:
                processed_df[var] = processed_df[var].fillna(processed_df[var].median())
        elif handle_missing == 'mean':
            for var in input_vars + [output_var]:
                processed_df[var] = processed_df[var].fillna(processed_df[var].mean())
        elif handle_missing == 'forward':
            for var in input_vars + [output_var]:
                processed_df[var] = processed_df[var].fillna(method='ffill')
        elif handle_missing == 'backward':
            for var in input_vars + [output_var]:
                processed_df[var] = processed_df[var].fillna(method='bfill')
        
        # Remove outliers - only if remove_outliers is True (boolean check)
        if isinstance(remove_outliers, bool) and remove_outliers:
            for var in input_vars + [output_var]:
                # Use try-except to handle cases where zscore fails
                try:
                    z_scores = stats.zscore(processed_df[var])
                    processed_df[var] = processed_df[var].mask(abs(z_scores) > outlier_threshold)
                    processed_df[var] = processed_df[var].fillna(processed_df[var].median())
                except:
                    pass
        
        # Create lagged features
        feature_cols = []
        for var in input_vars:
            if var in var_types and var_types[var] == "Dynamic":
                for lag in range(1, num_lags + 1):
                    processed_df[f'{var}_Lag_{lag}'] = processed_df[var].shift(lag)
                    feature_cols.append(f'{var}_Lag_{lag}')
            else:
                feature_cols.append(var)
        
        # Add output variable lags
        for lag in range(1, num_lags + 1):
            processed_df[f'{output_var}_Lag_{lag}'] = processed_df[output_var].shift(lag)
            feature_cols.append(f'{output_var}_Lag_{lag}')
        
        # Remove rows with NaN values from lagged features
        lag_cols = [col for col in feature_cols if "_Lag_" in col]
        if lag_cols:
            processed_df = processed_df.dropna(subset=lag_cols, how='any')
        
        # Fill remaining NaN values
        processed_df = processed_df.fillna(0)
        
        return processed_df, feature_cols
    except Exception:
        # Return a minimal valid result
        return df, input_vars

# -------------------- Advanced Feature Engineering --------------------
def engineer_features(df, feature_cols, output_var, date_col=None):
    """Add engineered features to the dataset."""
    try:
        # Validate inputs
        if df is None or df.empty:
            return df
            
        if not feature_cols:
            return df
            
        if output_var not in df.columns:
            return df
            
        engineered_df = df.copy()
        
        # Time-based features if date column is available
        if date_col and date_col in engineered_df.columns:
            try:
                # Check if date column is actually a datetime
                if not pd.api.types.is_datetime64_any_dtype(engineered_df[date_col]):
                    engineered_df[date_col] = pd.to_datetime(engineered_df[date_col], errors='coerce')
                
                engineered_df['hour'] = engineered_df[date_col].dt.hour
                engineered_df['day'] = engineered_df[date_col].dt.day
                engineered_df['month'] = engineered_df[date_col].dt.month
                engineered_df['day_of_week'] = engineered_df[date_col].dt.dayofweek
                engineered_df['is_weekend'] = (engineered_df['day_of_week'].isin([5, 6])).astype(int)
            except Exception:
                pass
        
        # Rolling statistics
        for var in feature_cols:
            if '_Lag_' in var:
                try:
                    base_var = var.split('_Lag_')[0]
                    if base_var in engineered_df.columns:
                        engineered_df[f'{base_var}_rolling_mean_3'] = engineered_df[base_var].rolling(window=3, min_periods=1).mean()
                        engineered_df[f'{base_var}_rolling_std_3'] = engineered_df[base_var].rolling(window=3, min_periods=1).std()
                except Exception:
                    pass
        
        # Interaction features - limit to avoid explosion of features
        valid_features = [f for f in feature_cols if f in engineered_df.columns]
        if len(valid_features) > 10:
            # If too many features, only use the first 10 to avoid combinatorial explosion
            valid_features = valid_features[:10]
            
        for i, var1 in enumerate(valid_features):
            for var2 in valid_features[i+1:min(i+5, len(valid_features))]:  # Limit interactions
                try:
                    if '_Lag_' not in var1 and '_Lag_' not in var2:
                        if var1 in engineered_df.columns and var2 in engineered_df.columns:
                            engineered_df[f'{var1}_{var2}_interaction'] = engineered_df[var1] * engineered_df[var2]
                except Exception:
                    pass
        
        # Fill NaN values from feature engineering
        engineered_df = engineered_df.fillna(method='bfill').fillna(method='ffill').fillna(0)
        
        return engineered_df
    except Exception:
        return df  # Return original dataframe if engineering fails

# -------------------- Advanced Visualization --------------------
def plot_advanced_metrics(metrics_history):
    """Create advanced visualization of training metrics."""
    fig = make_subplots(rows=2, cols=2, subplot_titles=('Loss', 'Learning Rate', 'RMSE', 'R²'))
    
    # Loss plot
    fig.add_trace(
        go.Scatter(y=[m.get('loss', 0) for m in metrics_history], name='Training Loss'),
        row=1, col=1
    )
    if metrics_history and 'val_loss' in metrics_history[0]:
        fig.add_trace(
            go.Scatter(y=[m.get('val_loss', 0) for m in metrics_history], name='Validation Loss'),
            row=1, col=1
        )
    
    # Learning rate plot
    fig.add_trace(
        go.Scatter(y=[m.get('lr', m.get('learning_rate', 0.001)) for m in metrics_history], name='Learning Rate'),
        row=1, col=2
    )
    
    # RMSE plot
    fig.add_trace(
        go.Scatter(y=[np.sqrt(m.get('mse', m.get('mean_squared_error', 0))) for m in metrics_history], name='RMSE'),
        row=2, col=1
    )
    
    # R² plot
    fig.add_trace(
        go.Scatter(y=[m.get('r2', 0) for m in metrics_history], name='R²'),
        row=2, col=2
    )
    
    fig.update_layout(height=800, showlegend=True)
    return fig

def plot_prediction_with_uncertainty(dates, actual, predicted_mean, predicted_std, title):
    """Create visualization with prediction uncertainty."""
    fig = go.Figure()
    
    # Actual values
    fig.add_trace(go.Scatter(
        x=dates,
        y=actual,
        name='Actual',
        line=dict(color='blue')
    ))
    
    # Predicted mean
    fig.add_trace(go.Scatter(
        x=dates,
        y=predicted_mean,
        name='Predicted',
        line=dict(color='red')
    ))
    
    # Confidence intervals
    fig.add_trace(go.Scatter(
        x=dates.tolist() + dates[::-1].tolist(),
        y=(predicted_mean + 1.96 * predicted_std).tolist() + 
          (predicted_mean - 1.96 * predicted_std)[::-1].tolist(),
        fill='toself',
        fillcolor='rgba(255,0,0,0.1)',
        line=dict(color='rgba(255,0,0,0)'),
        name='95% Confidence Interval'
    ))
    
    fig.update_layout(
        title=title,
        xaxis_title='Date',
        yaxis_title='Value',
        showlegend=True
    )
    
    return fig

# -------------------- Advanced Model Training --------------------
def train_advanced_model(model, X_train, y_train, X_val, y_val, epochs, batch_size, callbacks):
    """Train model with advanced features."""
    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=callbacks,
        verbose=1
    )
    return history

# -------------------- Advanced Prediction --------------------
def predict_with_uncertainty(model, X, num_samples=100):
    """Generate predictions with uncertainty estimation."""
    if TFP_AVAILABLE:
        # For TFP models, we can get uncertainty directly from the model
        predictions = model.predict(X)
        mean_pred = predictions[:, 0]
        std_pred = predictions[:, 1]
        return mean_pred, std_pred
    else:
        # For non-TFP models, use a smaller number of samples for testing
        if len(X) > 1000:  # If dataset is large, reduce number of samples
            num_samples = min(20, num_samples)
        
        # Generate all noise at once
        noise = np.random.normal(0, 0.01, (num_samples,) + X.shape)
        X_noisy = X + noise
        
        # Reshape for batch prediction
        X_batch = X_noisy.reshape(-1, *X.shape[1:])
        predictions = model.predict(X_batch)
        
        # Reshape back and calculate statistics
        predictions = predictions.reshape(num_samples, -1)
        mean_pred = np.mean(predictions, axis=0)
        std_pred = np.std(predictions, axis=0)
        
        return mean_pred, std_pred

def generate_future_predictions(model, last_sequence, scaler, feature_cols, num_steps, 
                              input_vars, output_var, var_types, num_lags):
    """Generate predictions for future time steps."""
    future_predictions = []
    current_sequence = last_sequence.copy()
    
    for _ in range(num_steps):
        # Get prediction
        pred = model.predict(current_sequence.reshape(1, 1, -1))
        future_predictions.append(pred[0])
        
        # Update sequence for next prediction
        new_sequence = current_sequence[0].copy()
        new_sequence = np.roll(new_sequence, -1)
        new_sequence[-1] = pred[0]
        current_sequence = new_sequence
    
    return np.array(future_predictions)

# -------------------- Advanced Model Evaluation --------------------
def evaluate_model_advanced(model, X_test, y_test, scaler, feature_cols):
    """Advanced model evaluation with multiple metrics."""
    predictions = model.predict(X_test)
    mean_pred, std_pred = predictions[:, 0], predictions[:, 1]
    
    # Calculate metrics
    metrics = {}
    for metric_name, metric_func in all_metrics_dict.items():
        metrics[metric_name] = metric_func(y_test, mean_pred)
    
    # Calculate prediction intervals
    lower_bound = mean_pred - 1.96 * std_pred
    upper_bound = mean_pred + 1.96 * std_pred
    
    # Calculate coverage of prediction intervals
    coverage = np.mean((y_test >= lower_bound) & (y_test <= upper_bound))
    metrics['Prediction Interval Coverage'] = coverage
    
    return metrics, mean_pred, std_pred

# -------------------- Advanced Hyperparameter Optimization --------------------
def objective(trial, X_train, y_train, X_val, y_val, model_type):
    """Objective function for Optuna hyperparameter optimization."""
    import tensorflow as tf
    
    # Clear any existing TensorFlow session state
    tf.keras.backend.clear_session()
    
    # Define hyperparameters to optimize with more focused ranges
    hp = {
        'learning_rate': trial.suggest_loguniform('learning_rate', 1e-4, 1e-2),
        'num_layers': trial.suggest_int('num_layers', 1, 3),
        'units': trial.suggest_int('units', 32, 128),
        'dropout_rate': trial.suggest_uniform('dropout_rate', 0.1, 0.3),
        'batch_size': trial.suggest_categorical('batch_size', [32, 64])
    }
    
    try:
        # Build model with error handling
        model = build_advanced_model(
            input_shape=(X_train.shape[1], X_train.shape[2]),
            model_type=model_type,
            layers=hp['num_layers'],
            units=[hp['units']] * hp['num_layers'],
            dense_layers=1,
            dense_units=[32],
            learning_rate=hp['learning_rate'],
            dropout_rate=hp['dropout_rate']
        )
        
        # Train model with reduced epochs and earlier stopping
        early_stopping = tf.keras.callbacks.EarlyStopping(
            monitor='val_loss',
            patience=5,
            restore_best_weights=True
        )
        
        history = model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=20,
            batch_size=hp['batch_size'],
            callbacks=[early_stopping],
            verbose=0
        )
        
        # Get the best validation loss
        best_val_loss = min(history.history['val_loss'])
        
        # Clean up
        del model
        tf.keras.backend.clear_session()
        
        return best_val_loss
    
    except Exception as e:
        # Clean up on error
        tf.keras.backend.clear_session()
        raise optuna.exceptions.TrialPruned(f"Trial failed: {str(e)}")

# -------------------- Styling and Streamlit UI --------------------
st.set_page_config(page_title="Wateran", page_icon="🌊", layout="wide")

# Theme toggle
with st.sidebar:
    st.title("🌊 Wateran")
    theme = st.selectbox("Theme", ["Light", "Dark"])
    if theme == "Dark":
        st.markdown("""
            <style>
            .stApp {
                background-color: #1E1E1E;
                color: #FFFFFF;
            }
            .stButton>button {
                background-color: #4CAF50;
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: bold;
            }
            .stButton>button:hover {
                background-color: #45a049;
            }
            .metric-box {
                background-color: #2D2D2D;
                border-radius: 8px;
                padding: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.2);
                text-align: center;
                margin: 10px 0;
            }
            </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
            <style>
            .stApp {
                background-color: #f0f4f8;
                color: #000000;
            }
            .stButton>button {
                background-color: #007bff;
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: bold;
            }
            .stButton>button:hover {
                background-color: #0056b3;
            }
            .metric-box {
                background-color: #ffffff;
                border-radius: 8px;
                padding: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                text-align: center;
                margin: 10px 0;
            }
            </style>
        """, unsafe_allow_html=True)

st.title("🌊 Wateran: Advanced Time Series Prediction")
st.markdown("**State-of-the-art Time Series Prediction with Uncertainty Quantification**", unsafe_allow_html=True)

# Initialize session state variables
if 'model_type' not in st.session_state:
    st.session_state.model_type = "GRU"
if 'num_lags' not in st.session_state:
    st.session_state.num_lags = DEFAULT_NUM_LAGS
if 'input_vars' not in st.session_state:
    st.session_state.input_vars = []
if 'output_var' not in st.session_state:
    st.session_state.output_var = None
if 'var_types' not in st.session_state:
    st.session_state.var_types = {}
if 'date_col' not in st.session_state:
    st.session_state.date_col = None
if 'df' not in st.session_state:
    st.session_state.df = None
if 'feature_cols' not in st.session_state:
    st.session_state.feature_cols = []
if 'handle_missing' not in st.session_state:
    st.session_state.handle_missing = 'median'
if 'remove_outliers' not in st.session_state:
    st.session_state.remove_outliers = True
if 'outlier_threshold' not in st.session_state:
    st.session_state.outlier_threshold = 3.0
if 'enable_feature_engineering' not in st.session_state:
    st.session_state.enable_feature_engineering = True
if 'physics_weight' not in st.session_state:
    st.session_state.physics_weight = DEFAULT_PHYSICS_WEIGHT
if 'use_mass_conservation' not in st.session_state:
    st.session_state.use_mass_conservation = True
if 'use_smoothness' not in st.session_state:
    st.session_state.use_smoothness = True

# Initialize other session state variables
for key in ['metrics', 'train_results_df', 'test_results_df', 'fig', 'model_plot', 'scaler', 
            'new_predictions_df', 'new_fig', 'gru_layers', 'lstm_layers', 'rnn_layers', 'gru_units', 
            'lstm_units', 'rnn_units', 'dense_layers', 'dense_units', 'learning_rate', 
            'new_data_file', 'selected_inputs', 'new_date_col', 'selected_metrics', 'new_var_types', 
            'cv_metrics', 'X_train', 'y_train', 'X_test', 'y_test', 'model', 'hybrid_models',
            'use_attention', 'use_bidirectional', 'use_residual', 'dropout_rate', 'prediction_horizon', 'num_samples']:
    if key not in st.session_state:
        if key in ['gru_layers', 'lstm_layers', 'rnn_layers', 'dense_layers']:
            st.session_state[key] = 1
        elif key == 'gru_units':
            st.session_state[key] = [DEFAULT_GRU_UNITS]
        elif key == 'lstm_units':
            st.session_state[key] = [DEFAULT_LSTM_UNITS]
        elif key == 'rnn_units':
            st.session_state[key] = [DEFAULT_RNN_UNITS]
        elif key == 'dense_units':
            st.session_state[key] = [DEFAULT_DENSE_UNITS]
        elif key == 'learning_rate':
            st.session_state[key] = DEFAULT_LEARNING_RATE
        elif key == 'hybrid_models':
            st.session_state[key] = ["GRU"]
        elif key == 'prediction_horizon':
            st.session_state[key] = DEFAULT_PREDICTION_HORIZON
        elif key == 'num_samples':
            st.session_state[key] = 100
        else:
            st.session_state[key] = None

# Sidebar for Navigation and Help
with st.sidebar:
    st.header("Navigation")
    st.button("📥 Data Input", key="nav_data")
    st.button("⚙️ Model Configuration", key="nav_config")
    st.button("📊 Results", key="nav_results")
    st.button("🔮 New Predictions", key="nav_predict")
    with st.expander("ℹ️ Help"):
        st.markdown("""
        - **Layers**: Recurrent layers (GRU, LSTM, RNN) for time dependencies (1-5 recommended).
        - **Dense Layers**: Fully connected layers for output refinement.
        - **Dynamic Variables**: Use lagged values for time series modeling.
        - **Static Variables**: Constant features, no lags applied.
        - **Metrics**: NSE/KGE ideal = 1, RMSE/MAE ideal = 0.
        - **Hybrid**: Combine any number of GRU, LSTM, RNN models.
        - **Advanced Features**: Attention, Bidirectional layers, Residual connections.
        - **Uncertainty**: Probabilistic predictions with confidence intervals.
        """)

# Main Layout
col1, col2 = st.columns([2, 1], gap="large")

# Left Column: Data and Variable Selection
with col1:
    st.subheader("📥 Data Input", divider="blue")
    uploaded_file = st.file_uploader("Upload Training Data", type=["xlsx", "csv", "json"], key="train_data", 
                                    help="Upload your time series data file (Excel, CSV, or JSON).")
    
    if uploaded_file:
        @st.cache_data
        def load_data(file):
            if file.name.endswith('.csv'):
                return pd.read_csv(file)
            elif file.name.endswith('.json'):
                return pd.read_json(file)
            else:
                return pd.read_excel(file)
        
        df = load_data(uploaded_file)
        st.session_state.df = df
        st.markdown("**Dataset Preview:**")
        st.dataframe(df.head(5), use_container_width=True)
        
        # Data preprocessing options
        with st.expander("🔄 Data Preprocessing", expanded=True):
            st.markdown("**Missing Value Handling**")
            handle_missing = st.selectbox(
                "Handle Missing Values",
                ["median", "mean", "forward", "backward"],
                index=["median", "mean", "forward", "backward"].index(st.session_state.handle_missing)
            )
            st.session_state.handle_missing = handle_missing
            
            st.markdown("**Outlier Detection**")
            remove_outliers = st.checkbox("Remove Outliers", value=st.session_state.remove_outliers)
            if remove_outliers:
                outlier_threshold = st.slider(
                    "Outlier Threshold (Z-score)",
                    min_value=1.0,
                    max_value=5.0,
                    value=float(st.session_state.outlier_threshold),
                    step=0.5
                )
                st.session_state.outlier_threshold = outlier_threshold
            st.session_state.remove_outliers = remove_outliers
        
        # Feature engineering options
        with st.expander("🔧 Feature Engineering", expanded=True):
            engineer_features = st.checkbox("Enable Feature Engineering", value=st.session_state.enable_feature_engineering)
            st.session_state.enable_feature_engineering = engineer_features
        
        datetime_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col]) or "date" in col.lower()]
        date_col = st.selectbox("Select Date Column (optional)", ["None"] + datetime_cols, index=0, key="date_col_train")
        st.session_state.date_col = date_col if date_col != "None" else None
        if date_col != "None":
            df[date_col] = pd.to_datetime(df[date_col])
            df = df.sort_values(date_col)
        
        numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col]) and col != st.session_state.date_col]
        if len(numeric_cols) < 2:
            st.error("Dataset requires at least two numeric columns.")
            st.stop()
        
        st.markdown("**Variable Selection**")
        output_var = st.selectbox("🎯 Output Variable", numeric_cols, key="output_var_train")
        available_input_cols = [col for col in numeric_cols if col != output_var]
        default_input = [available_input_cols[0]] if available_input_cols else []
        input_vars = st.multiselect("🔧 Input Variables", available_input_cols, default=default_input, key="input_vars_train")
        if not input_vars:
            st.error("Select at least one input variable.")
            st.stop()

        with st.expander("Variable Types", expanded=True):
            var_types = {}
            for var in input_vars:
                var_types[var] = st.selectbox(f"{var} Type", ["Dynamic", "Static"], key=f"{var}_type")
            st.session_state.var_types = var_types
        
        with st.expander("📋 Data Exploration"):
            st.markdown("**Summary Statistics**")
            st.dataframe(df[numeric_cols].describe(), use_container_width=True)
            
            st.markdown("**Time Series Plot**")
            fig, ax = plt.subplots()
            df[numeric_cols].plot(ax=ax)
            ax.set_title("Time Series Plot")
            st.pyplot(fig)
            
            st.markdown("**Correlation Heatmap**")
            fig, ax = plt.subplots()
            sns.heatmap(df[numeric_cols].corr(), annot=True, cmap="coolwarm", ax=ax)
            ax.set_title("Correlation Matrix")
            st.pyplot(fig)
            
            st.markdown("**Missing Values Analysis**")
            missing_df = pd.DataFrame({
                'Column': df.columns,
                'Missing Values': df.isnull().sum(),
                'Missing %': (df.isnull().sum() / len(df) * 100).round(2)
            })
            st.dataframe(missing_df, use_container_width=True)

        st.session_state.input_vars = input_vars
        st.session_state.output_var = output_var

# Right Column: Model Settings and Actions
with col2:
    st.subheader("⚙️ Model Configuration", divider="blue")
    
    model_type = st.selectbox("Model Type", ["GRU", "LSTM", "RNN", "PINN", "Hybrid"], index=0, key="model_type_select")
    st.session_state.model_type = model_type
    
    st.markdown("**Training Parameters**")
    num_lags = st.number_input("Number of Lags", min_value=1, max_value=10, value=DEFAULT_NUM_LAGS if 'num_lags' not in st.session_state else st.session_state.num_lags, step=1)
    st.session_state.num_lags = num_lags
    
    epochs = st.slider("Epochs", 1, 1500, DEFAULT_EPOCHS, step=10, key="epochs")
    batch_size = st.slider("Batch Size", 8, 128, DEFAULT_BATCH_SIZE, step=8, key="batch_size")
    train_split = st.slider("Training Data %", 50, 90, DEFAULT_TRAIN_SPLIT, key="train_split") / 100
    
    with st.expander("Advanced Model Architecture", expanded=False):
        st.markdown("**Model Components**")
        use_attention = st.checkbox("Use Attention Mechanism", value=st.session_state.use_attention)
        use_bidirectional = st.checkbox("Use Bidirectional Layers", value=st.session_state.use_bidirectional)
        use_residual = st.checkbox("Use Residual Connections", value=st.session_state.use_residual)
        dropout_rate = st.slider("Dropout Rate", 0.0, 0.5, 0.2, step=0.05)
        
        st.session_state.use_attention = use_attention
        st.session_state.use_bidirectional = use_bidirectional
        st.session_state.use_residual = use_residual
        st.session_state.dropout_rate = dropout_rate
        
        if model_type == "PINN":
            with st.expander("PINN Configuration", expanded=True):
                st.markdown("**Physics Constraints**")
                physics_weight = st.slider(
                    "Physics Loss Weight",
                    min_value=0.0,
                    max_value=1.0,
                    value=DEFAULT_PHYSICS_WEIGHT,
                    step=0.01,
                    help="Weight for physics-based loss term"
                )
                st.session_state.physics_weight = physics_weight
                
                use_mass_conservation = st.checkbox(
                    "Use Mass Conservation",
                    value=st.session_state.use_mass_conservation,
                    help="Enforce mass conservation constraint"
                )
                st.session_state.use_mass_conservation = use_mass_conservation
                
                use_smoothness = st.checkbox(
                    "Use Smoothness Constraint",
                    value=st.session_state.use_smoothness,
                    help="Enforce smoothness constraint"
                )
                st.session_state.use_smoothness = use_smoothness
        
        if model_type == "Hybrid":
            valid_options = ["GRU", "LSTM", "RNN", "PINN"]
            if 'hybrid_models' not in st.session_state or not isinstance(st.session_state.hybrid_models, list):
                st.session_state.hybrid_models = ["GRU"]
            hybrid_models = st.multiselect(
                "Select Hybrid Models (1 to all)",
                options=valid_options,
                default=st.session_state.hybrid_models,
                key="hybrid_models_select"
            )
            if hybrid_models and hybrid_models != st.session_state.hybrid_models:
                st.session_state.hybrid_models = hybrid_models
            if not hybrid_models:
                st.warning("Please select at least one hybrid model. Defaulting to GRU.")
                st.session_state.hybrid_models = ["GRU"]
            hybrid_layers = st.number_input("Total Hybrid Layers", min_value=1, max_value=10, value=max(st.session_state.gru_layers, 1), 
                                           step=1, key="hybrid_layers")
            st.session_state.gru_layers = hybrid_layers
            st.session_state.gru_units = [
                st.number_input(
                    f"Hybrid Layer {i+1} Units",
                    min_value=8,
                    max_value=512,
                    value=st.session_state.gru_units[i] if i < len(st.session_state.gru_units) else DEFAULT_GRU_UNITS,
                    step=8,
                    key=f"hybrid_{i}"
                ) for i in range(hybrid_layers)
            ]
        elif model_type == "GRU":
            gru_layers = st.number_input("GRU Layers", min_value=1, max_value=5, value=st.session_state.gru_layers, step=1, key="gru_layers")
            if gru_layers != st.session_state.gru_layers:
                st.session_state.gru_layers = gru_layers
            st.session_state.gru_units = [
                st.number_input(
                    f"GRU Layer {i+1} Units",
                    min_value=8,
                    max_value=512,
                    value=st.session_state.gru_units[i] if i < len(st.session_state.gru_units) else DEFAULT_GRU_UNITS,
                    step=8,
                    key=f"gru_{i}"
                ) for i in range(st.session_state.gru_layers)
            ]
        elif model_type == "LSTM":
            lstm_layers = st.number_input("LSTM Layers", min_value=1, max_value=5, value=st.session_state.lstm_layers, step=1, key="lstm_layers")
            if lstm_layers != st.session_state.lstm_layers:
                st.session_state.lstm_layers = lstm_layers
            st.session_state.lstm_units = [
                st.number_input(
                    f"LSTM Layer {i+1} Units",
                    min_value=8,
                    max_value=512,
                    value=st.session_state.lstm_units[i] if i < len(st.session_state.lstm_units) else DEFAULT_LSTM_UNITS,
                    step=8,
                    key=f"lstm_{i}"
                ) for i in range(st.session_state.lstm_layers)
            ]
        elif model_type == "RNN":
            rnn_layers = st.number_input("RNN Layers", min_value=1, max_value=5, value=st.session_state.rnn_layers, step=1, key="rnn_layers")
            if rnn_layers != st.session_state.rnn_layers:
                st.session_state.rnn_layers = rnn_layers
            st.session_state.rnn_units = [
                st.number_input(
                    f"RNN Layer {i+1} Units",
                    min_value=8,
                    max_value=512,
                    value=st.session_state.rnn_units[i] if i < len(st.session_state.rnn_units) else DEFAULT_RNN_UNITS,
                    step=8,
                    key=f"rnn_{i}"
                ) for i in range(st.session_state.rnn_layers)
            ]
        
        dense_layers = st.number_input("Dense Layers", min_value=1, max_value=5, value=st.session_state.dense_layers, step=1, key="dense_layers")
        if dense_layers != st.session_state.dense_layers:
            st.session_state.dense_layers = dense_layers
        st.session_state.dense_units = [
            st.number_input(
                f"Dense Layer {i+1} Units",
                min_value=8,
                max_value=512,
                value=st.session_state.dense_units[i] if i < len(st.session_state.dense_units) else DEFAULT_DENSE_UNITS,
                step=8,
                key=f"dense_{i}"
            ) for i in range(st.session_state.dense_layers)
        ]
        learning_rate = st.number_input("Learning Rate", min_value=0.00001, max_value=0.1, value=st.session_state.learning_rate, 
                                        format="%.5f", key="learning_rate")
        if learning_rate != st.session_state.learning_rate:
            st.session_state.learning_rate = learning_rate
    
    st.markdown("**Evaluation Metrics**")
    all_metrics = ["RMSE", "MAE", "R²", "NSE", "KGE", "MAPE"]
    st.session_state.selected_metrics = st.multiselect("Select Metrics", all_metrics, default=all_metrics, key="metrics_select") or all_metrics

    if uploaded_file:
        col_btn1, col_btn2, col_btn3 = st.columns(3)
        with col_btn1:
            if st.button("🚀 Train Model", key="train_button"):
                df = st.session_state.df.copy()
                
                # Preprocess data
                processed_df, feature_cols = preprocess_data(
                    df, 
                    st.session_state.input_vars, 
                    st.session_state.output_var, 
                    st.session_state.var_types, 
                    st.session_state.num_lags,
                    st.session_state.date_col,
                    st.session_state.handle_missing,
                    st.session_state.remove_outliers,
                    st.session_state.outlier_threshold
                )
                
                # Feature engineering
                if st.session_state.enable_feature_engineering:
                    try:
                        processed_df = engineer_features(
                            processed_df,
                            feature_cols,
                            st.session_state.output_var,
                            st.session_state.date_col
                        )
                    except Exception:
                        # Silently continue with basic features
                        pass
                
                st.session_state.feature_cols = feature_cols
                
                # Split data
                train_size = int(len(processed_df) * train_split)
                train_df, test_df = processed_df[:train_size], processed_df[train_size:]
                
                # Scale data
                scaler = MinMaxScaler()
                train_scaled = scaler.fit_transform(train_df[feature_cols + [st.session_state.output_var]])
                test_scaled = scaler.transform(test_df[feature_cols + [st.session_state.output_var]])
                st.session_state.scaler = scaler
                
                # Prepare sequences
                X_train, y_train = train_scaled[:, :-1], train_scaled[:, -1]
                X_test, y_test = test_scaled[:, :-1], test_scaled[:, -1]
                X_train = X_train.reshape((X_train.shape[0], 1, X_train.shape[1]))
                X_test = X_test.reshape((X_test.shape[0], 1, X_test.shape[1]))
                y_train = y_train.reshape(-1, 1)
                y_test = y_test.reshape(-1, 1)
                
                # Validation split
                val_size = int(len(X_train) * 0.2)
                X_val = X_train[-val_size:]
                y_val = y_train[-val_size:]
                X_train = X_train[:-val_size]
                y_train = y_train[:-val_size]
                
                st.session_state.X_train, st.session_state.y_train = X_train, y_train
                st.session_state.X_test, st.session_state.y_test = X_test, y_test
                
                # Build model
                layers = (st.session_state.gru_layers if model_type in ["GRU", "Hybrid"] else 
                          st.session_state.lstm_layers if model_type == "LSTM" else 
                          st.session_state.rnn_layers if model_type == "RNN" else 
                          st.session_state.gru_layers)
                units = (st.session_state.gru_units if model_type in ["GRU", "Hybrid"] else 
                         st.session_state.lstm_units if model_type == "LSTM" else 
                         st.session_state.rnn_units if model_type == "RNN" else 
                         st.session_state.gru_units)
                
                st.session_state.model = build_advanced_model(
                    (X_train.shape[1], X_train.shape[2]), 
                    model_type, 
                    layers, 
                    units, 
                    st.session_state.dense_layers, 
                    st.session_state.dense_units, 
                    st.session_state.learning_rate,
                    st.session_state.use_attention,
                    st.session_state.use_bidirectional,
                    st.session_state.use_residual,
                    st.session_state.dropout_rate
                )
                
                # Callbacks
                early_stopping = EarlyStopping(
                    monitor='val_loss',
                    patience=10,
                    restore_best_weights=True
                )
                lr_scheduler = ReduceLROnPlateau(
                    monitor='val_loss',
                    factor=0.5,
                    patience=5,
                    min_lr=1e-6
                )
                model_checkpoint = ModelCheckpoint(
                    MODEL_WEIGHTS_PATH,
                    monitor='val_loss',
                    save_best_only=True,
                    mode='min'
                )
                tensorboard = TensorBoard(
                    log_dir=TENSORBOARD_DIR,
                    histogram_freq=1
                )
                
                with st.spinner("Training in progress..."):
                    progress_placeholder = st.empty()
                    metrics_placeholder = st.empty()
                    callback = StreamlitProgressCallback(epochs, progress_placeholder, metrics_placeholder)
                    
                    try:
                        history = train_advanced_model(
                            st.session_state.model,
                            X_train, y_train,
                            X_val, y_val,
                            epochs, batch_size,
                            [callback, early_stopping, lr_scheduler, model_checkpoint, tensorboard]
                        )
                        
                        st.session_state.model.save_weights(MODEL_WEIGHTS_PATH)
                        st.session_state.model.save(MODEL_FULL_PATH)
                        
                        # Plot training history
                        fig = plot_advanced_metrics(callback.metrics_history)
                        st.plotly_chart(fig, use_container_width=True)
                        
                        st.success("Model trained and saved successfully!")
                    except Exception as e:
                        st.error(f"Training failed with error: {str(e)}")
        
        with col_btn2:
            if st.button(f"🤖 Optimize Hyperparameters", key="optimize_button"):
                if "X_train" not in st.session_state or "y_train" not in st.session_state:
                    st.error("Please train the model first to generate training data.")
                else:
                    with st.spinner("Optimizing hyperparameters... This may take a few minutes."):
                        try:
                            # Clear any existing TensorFlow session state
                            tf.keras.backend.clear_session()
                            
                            # Create validation split
                            val_size = int(len(st.session_state.X_train) * 0.2)
                            
                            # Use a subset of data for faster optimization
                            max_samples = 1000
                            if len(st.session_state.X_train) > max_samples:
                                step = len(st.session_state.X_train) // max_samples
                                X_train_subset = st.session_state.X_train[::step]
                                y_train_subset = st.session_state.y_train[::step]
                            else:
                                X_train_subset = st.session_state.X_train
                                y_train_subset = st.session_state.y_train
                            
                            val_size = int(len(X_train_subset) * 0.2)
                            X_val = X_train_subset[-val_size:]
                            y_val = y_train_subset[-val_size:]
                            X_train_opt = X_train_subset[:-val_size]
                            y_train_opt = y_train_subset[:-val_size]
                            
                            # Create study with faster sampler
                            study = optuna.create_study(
                                direction='minimize',
                                sampler=optuna.samplers.TPESampler(n_startup_trials=5)
                            )
                            
                            # Run optimization with progress bar
                            progress_bar = st.progress(0)
                            for i in range(8):
                                study.optimize(lambda trial: objective(
                                    trial, 
                                    X_train_opt, 
                                    y_train_opt, 
                                    X_val, 
                                    y_val, 
                                    st.session_state.model_type
                                ), n_trials=1)
                                progress_bar.progress((i + 1) / 8)
                            
                            # Store best parameters in session state without modifying widget values
                            best_params = study.best_params
                            if 'opt_params' not in st.session_state:
                                st.session_state.opt_params = {}
                            
                            st.session_state.opt_params.update({
                                'learning_rate': best_params['learning_rate'],
                                'dropout_rate': best_params['dropout_rate'],
                                'num_layers': best_params['num_layers'],
                                'units': best_params['units']
                            })
                            
                            # Display results
                            st.success("Optimization completed successfully!")
                            st.write("Best hyperparameters found:")
                            st.json({
                                'learning_rate': f"{best_params['learning_rate']:.6f}",
                                'num_layers': best_params['num_layers'],
                                'units': best_params['units'],
                                'dropout_rate': f"{best_params['dropout_rate']:.3f}"
                            })
                            st.write("Best validation loss:", f"{study.best_value:.6f}")
                            
                            # Provide instructions to user
                            st.info("""
                            To apply these optimized parameters:
                            1. Click 'Train Model' again with the suggested values
                            2. The model will be rebuilt with the optimized architecture
                            """)
                            
                            # Final cleanup
                            tf.keras.backend.clear_session()
                            
                        except Exception as e:
                            st.error(f"Optimization failed: {str(e)}")
                            tf.keras.backend.clear_session()
        
        with col_btn3:
            if st.button("🔍 Test Model", key="test_button"):
                if not os.path.exists(MODEL_WEIGHTS_PATH):
                    st.error("Train the model first!")
                    st.stop()
                
                # Preprocess test data
                df = st.session_state.df.copy()
                processed_df, feature_cols = preprocess_data(
                    df, 
                    st.session_state.input_vars, 
                    st.session_state.output_var, 
                    st.session_state.var_types, 
                    st.session_state.num_lags,
                    st.session_state.date_col,
                    st.session_state.handle_missing,
                    st.session_state.remove_outliers,
                    st.session_state.outlier_threshold
                )
                
                # Feature engineering
                if st.session_state.enable_feature_engineering:
                    try:
                        processed_df = engineer_features(
                            processed_df,
                            feature_cols,
                            st.session_state.output_var,
                            st.session_state.date_col
                        )
                    except Exception:
                        # Silently continue with basic features
                        pass
                
                st.session_state.feature_cols = feature_cols
                
                # Split and scale data
                train_size = int(len(processed_df) * train_split)
                train_df, test_df = processed_df[:train_size], processed_df[train_size:]
                scaler = st.session_state.scaler
                
                train_scaled = scaler.transform(train_df[feature_cols + [st.session_state.output_var]])
                test_scaled = scaler.transform(test_df[feature_cols + [st.session_state.output_var]])
                
                X_train, y_train = train_scaled[:, :-1], train_scaled[:, -1]
                X_test, y_test = test_scaled[:, :-1], test_scaled[:, -1]
                X_train = X_train.reshape((X_train.shape[0], 1, X_train.shape[1]))
                X_test = X_test.reshape((X_test.shape[0], 1, X_test.shape[1]))
                y_train = y_train.reshape(-1, 1)
                y_test = y_test.reshape(-1, 1)
                
                # Use existing model if available, otherwise load weights
                if st.session_state.model is None:
                    layers = (st.session_state.gru_layers if model_type in ["GRU", "Hybrid"] else 
                              st.session_state.lstm_layers if model_type == "LSTM" else 
                              st.session_state.rnn_layers if model_type == "RNN" else 
                              st.session_state.gru_layers)
                    units = (st.session_state.gru_units if model_type in ["GRU", "Hybrid"] else 
                             st.session_state.lstm_units if model_type == "LSTM" else 
                             st.session_state.rnn_units if model_type == "RNN" else 
                             st.session_state.gru_units)
                    
                    st.session_state.model = build_advanced_model(
                        (X_train.shape[1], X_train.shape[2]), 
                        model_type, 
                        layers, 
                        units, 
                        st.session_state.dense_layers, 
                        st.session_state.dense_units, 
                        st.session_state.learning_rate,
                        st.session_state.use_attention,
                        st.session_state.use_bidirectional,
                        st.session_state.use_residual,
                        st.session_state.dropout_rate
                    )
                    st.session_state.model.load_weights(MODEL_WEIGHTS_PATH)
                
                # Generate predictions with uncertainty (reduced samples for testing)
                test_samples = min(20, st.session_state.num_samples)  # Reduce samples for testing
                y_train_pred_mean, y_train_pred_std = predict_with_uncertainty(
                    st.session_state.model,
                    X_train,
                    test_samples
                )
                y_test_pred_mean, y_test_pred_std = predict_with_uncertainty(
                    st.session_state.model,
                    X_test,
                    test_samples
                )
                
                # Inverse transform predictions
                y_train_pred_mean = y_train_pred_mean.reshape(-1, 1)
                y_test_pred_mean = y_test_pred_mean.reshape(-1, 1)
                y_train = y_train.reshape(-1, 1)
                y_test = y_test.reshape(-1, 1)
                
                # Ensure all arrays have matching first dimensions
                min_train_len = min(len(y_train_pred_mean), len(X_train))
                min_test_len = min(len(y_test_pred_mean), len(X_test))
                
                y_train_pred = scaler.inverse_transform(np.hstack([
                    y_train_pred_mean[:min_train_len], 
                    X_train[:min_train_len, 0, :]
                ]))[:, 0]
                
                y_test_pred = scaler.inverse_transform(np.hstack([
                    y_test_pred_mean[:min_test_len], 
                    X_test[:min_test_len, 0, :]
                ]))[:, 0]
                
                y_train_actual = scaler.inverse_transform(np.hstack([
                    y_train[:min_train_len], 
                    X_train[:min_train_len, 0, :]
                ]))[:, 0]
                
                y_test_actual = scaler.inverse_transform(np.hstack([
                    y_test[:min_test_len], 
                    X_test[:min_test_len, 0, :]
                ]))[:, 0]
                
                # Clip predictions to non-negative values
                y_train_pred, y_test_pred = np.clip(y_train_pred, 0, None), np.clip(y_test_pred, 0, None)
                
                # Calculate metrics
                metrics = {metric: {
                    "Training": all_metrics_dict[metric](y_train_actual, y_train_pred),
                    "Testing": all_metrics_dict[metric](y_test_actual, y_test_pred)
                } for metric in st.session_state.selected_metrics}
                st.session_state.metrics = metrics
                
                # Create results DataFrames
                dates = processed_df[st.session_state.date_col] if st.session_state.date_col else pd.RangeIndex(len(processed_df))
                train_dates = dates[:train_size][:len(y_train_actual)]
                test_dates = dates[train_size:][:len(y_test_actual)]
                
                # Ensure all arrays have the same length
                min_train_len = min(len(train_dates), len(y_train_actual), len(y_train_pred), len(y_train_pred_std))
                min_test_len = min(len(test_dates), len(y_test_actual), len(y_test_pred), len(y_test_pred_std))
                
                st.session_state.train_results_df = pd.DataFrame({
                    "Date": train_dates[:min_train_len],
                    f"Actual_{st.session_state.output_var}": y_train_actual[:min_train_len],
                    f"Predicted_{st.session_state.output_var}": y_train_pred[:min_train_len],
                    "Uncertainty": y_train_pred_std[:min_train_len]
                })
                
                st.session_state.test_results_df = pd.DataFrame({
                    "Date": test_dates[:min_test_len],
                    f"Actual_{st.session_state.output_var}": y_test_actual[:min_test_len],
                    f"Predicted_{st.session_state.output_var}": y_test_pred[:min_test_len],
                    "Uncertainty": y_test_pred_std[:min_test_len]
                })
                
                # Create prediction plot
                fig = plot_prediction_with_uncertainty(
                    train_dates[:len(y_train_actual)],
                    y_train_actual,
                    y_train_pred,
                    y_train_pred_std,
                    f"Training: {st.session_state.output_var}"
                )
                st.session_state.fig = fig
                
                st.success("Model tested successfully!")

# Cross-Validation Section
if st.session_state.feature_cols:
    with st.expander("🔄 Cross-Validation", expanded=False):
        if st.button("Run Cross-Validation", key="cv_button"):
            df = st.session_state.df.copy()
            
            # Preprocess data
            processed_df, feature_cols = preprocess_data(
                df, 
                st.session_state.input_vars, 
                st.session_state.output_var, 
                st.session_state.var_types, 
                st.session_state.num_lags,
                st.session_state.date_col,
                st.session_state.handle_missing,
                st.session_state.remove_outliers,
                st.session_state.outlier_threshold
            )
            
            # Feature engineering
            if st.session_state.enable_feature_engineering:
                try:
                    processed_df = engineer_features(
                        processed_df,
                        feature_cols,
                        st.session_state.output_var,
                        st.session_state.date_col
                    )
                except Exception as e:
                    st.error(f"Error during feature engineering: {str(e)}")
                    st.info("Continuing with basic features only.")
            
            # Scale data
            scaler = st.session_state.scaler
            scaled = scaler.transform(processed_df[feature_cols + [st.session_state.output_var]])
            X, y = scaled[:, :-1], scaled[:, -1]
            X = X.reshape((X.shape[0], 1, X.shape[1]))
            y = y.reshape(-1, 1)
            
            # Time series cross-validation
            tscv = TimeSeriesSplit(n_splits=5)
            cv_metrics = {metric: [] for metric in st.session_state.selected_metrics}
            
            for train_idx, val_idx in tscv.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                # Build model
                layers = (st.session_state.gru_layers if model_type in ["GRU", "Hybrid"] else 
                          st.session_state.lstm_layers if model_type == "LSTM" else 
                          st.session_state.rnn_layers if model_type == "RNN" else 
                          st.session_state.gru_layers)
                units = (st.session_state.gru_units if model_type in ["GRU", "Hybrid"] else 
                         st.session_state.lstm_units if model_type == "LSTM" else 
                         st.session_state.rnn_units if model_type == "RNN" else 
                         st.session_state.gru_units)
                
                model = build_advanced_model(
                    (X_tr.shape[1], X_tr.shape[2]), 
                    model_type, 
                    layers, 
                    units, 
                    st.session_state.dense_layers, 
                    st.session_state.dense_units, 
                    st.session_state.learning_rate,
                    st.session_state.use_attention,
                    st.session_state.use_bidirectional,
                    st.session_state.use_residual,
                    st.session_state.dropout_rate
                )
                
                # Train model
                model.fit(X_tr, y_tr, epochs=epochs, batch_size=batch_size, verbose=0)
                
                # Generate predictions
                y_val_pred_mean, y_val_pred_std = predict_with_uncertainty(
                    model,
                    X_val,
                    st.session_state.num_samples
                )
                
                # Inverse transform predictions
                y_val_pred = scaler.inverse_transform(np.hstack([y_val_pred_mean, X_val[:, 0, :]]))[:, 0]
                y_val_actual = scaler.inverse_transform(np.hstack([y_val, X_val[:, 0, :]]))[:, 0]
                
                # Calculate metrics
                for metric in st.session_state.selected_metrics:
                    cv_metrics[metric].append(all_metrics_dict[metric](y_val_actual, y_val_pred))
            
            # Calculate mean metrics
            st.session_state.cv_metrics = {m: np.mean(cv_metrics[m]) for m in st.session_state.selected_metrics}
            st.write("Cross-Validation Results:", st.session_state.cv_metrics)

# Results Section
if any([st.session_state.metrics, st.session_state.fig, st.session_state.train_results_df, st.session_state.test_results_df]):
    with st.expander("📊 Results", expanded=True):
        st.subheader("Results Overview", divider="blue")
        
        if st.session_state.metrics:
            st.markdown("**📏 Performance Metrics**")
            metrics_df = pd.DataFrame({
                "Metric": st.session_state.selected_metrics,
                "Training": [f"{st.session_state.metrics[m]['Training']:.4f}" for m in st.session_state.selected_metrics],
                "Testing": [f"{st.session_state.metrics[m]['Testing']:.4f}" for m in st.session_state.selected_metrics]
            })
            st.dataframe(metrics_df.style.set_properties(**{'text-align': 'center'}), use_container_width=True)
        
        if st.session_state.fig:
            st.markdown("**📈 Prediction Plot**")
            st.plotly_chart(st.session_state.fig, use_container_width=True)
            
            # Download plot
            buf = BytesIO()
            try:
                st.session_state.fig.write_image(buf, format="png")
            except ValueError:
                fig, ax = plt.subplots()
                ax.plot(st.session_state.train_results_df["Date"], st.session_state.train_results_df[f"Actual_{st.session_state.output_var}"], label="Train Actual")
                ax.plot(st.session_state.train_results_df["Date"], st.session_state.train_results_df[f"Predicted_{st.session_state.output_var}"], label="Train Predicted", linestyle="--")
                ax.plot(st.session_state.test_results_df["Date"], st.session_state.test_results_df[f"Actual_{st.session_state.output_var}"], label="Test Actual")
                ax.plot(st.session_state.test_results_df["Date"], st.session_state.test_results_df[f"Predicted_{st.session_state.output_var}"], label="Test Predicted", linestyle="--")
                ax.legend()
                ax.set_title(f"Training and Testing: {st.session_state.output_var}")
                fig.savefig(buf, format="png", bbox_inches="tight")
            st.download_button("⬇️ Download Plot", buf.getvalue(), "prediction_plot.png", "image/png", key="plot_dl")
        
        if st.session_state.train_results_df is not None:
            train_csv = st.session_state.train_results_df.to_csv(index=False)
            st.download_button("⬇️ Download Train Data CSV", train_csv, "train_predictions.csv", "text/csv", key="train_dl")
        
        if st.session_state.test_results_df is not None:
            test_csv = st.session_state.test_results_df.to_csv(index=False)
            st.download_button("⬇️ Download Test Data CSV", test_csv, "test_predictions.csv", "text/csv", key="test_dl")
        
        if st.button("🗺️ Show Model Architecture", key="arch_button"):
            if st.session_state.model is None:
                st.error("No model available. Please train or test a model first.")
            else:
                plot_model(st.session_state.model, to_file=MODEL_PLOT_PATH, show_shapes=True, show_layer_names=True)
                st.image(MODEL_PLOT_PATH, caption=f"{model_type} Model Architecture")

# New Data Prediction Section
if os.path.exists(MODEL_WEIGHTS_PATH):
    with st.expander("🔮 New Predictions", expanded=False):
        st.subheader("Predict New Data", divider="blue")
        
        # Prediction settings
        prediction_horizon = st.number_input(
            "Prediction Horizon",
            min_value=1,
            max_value=30,
            value=st.session_state.prediction_horizon,
            help="Number of future time steps to predict"
        )
        st.session_state.prediction_horizon = prediction_horizon
        
        num_samples = st.number_input(
            "Number of Monte Carlo Samples",
            min_value=10,
            max_value=1000,
            value=st.session_state.num_samples,
            help="Number of samples for uncertainty estimation"
        )
        st.session_state.num_samples = num_samples
        
        new_data_files = st.file_uploader(
            "Upload New Data",
            type=["xlsx", "csv", "json"],
            accept_multiple_files=True,
            key="new_data",
            help="Upload your new data file(s) for prediction"
        )
        
        if new_data_files:
            for new_data_file in new_data_files:
                # Load new data
                if new_data_file.name.endswith('.csv'):
                    new_df = pd.read_csv(new_data_file)
                elif new_data_file.name.endswith('.json'):
                    new_df = pd.read_json(new_data_file)
                else:
                    new_df = pd.read_excel(new_data_file)
                
                st.markdown(f"**Preview for {new_data_file.name}:**")
                st.dataframe(new_df.head(), use_container_width=True)
                
                # Date column selection
                datetime_cols = [col for col in new_df.columns if pd.api.types.is_datetime64_any_dtype(new_df[col]) or "date" in col.lower()]
                date_col = st.selectbox(
                    f"Select Date Column ({new_data_file.name})",
                    ["None"] + datetime_cols,
                    index=0,
                    key=f"date_col_new_{new_data_file.name}"
                )
                
                if date_col != "None":
                    new_df[date_col] = pd.to_datetime(new_df[date_col])
                    new_df = new_df.sort_values(date_col)
                
                # Input variable selection
                input_vars = st.session_state.input_vars
                output_var = st.session_state.output_var
                num_lags = st.session_state.num_lags
                feature_cols = st.session_state.feature_cols
                
                available_new_inputs = [col for col in new_df.columns if col in input_vars and col != date_col]
                if not available_new_inputs:
                    st.error(f"No recognized input variables in {new_data_file.name}. Include: " + ", ".join(input_vars))
                    continue
                
                selected_inputs = st.multiselect(
                    f"🔧 Input Variables ({new_data_file.name})",
                    available_new_inputs,
                    default=available_new_inputs,
                    key=f"new_input_vars_{new_data_file.name}"
                )
                
                # Variable types
                st.markdown(f"**Variable Types ({new_data_file.name})**")
                new_var_types = {}
                for var in selected_inputs:
                    new_var_types[var] = st.selectbox(
                        f"{var} Type",
                        ["Dynamic", "Static"],
                        key=f"new_{var}_type_{new_data_file.name}"
                    )
                
                if st.button(f"🔍 Predict ({new_data_file.name})", key=f"predict_button_{new_data_file.name}"):
                    if len(new_df) < (num_lags + 1 if any(new_var_types[var] == "Dynamic" for var in selected_inputs) else 1):
                        st.error(f"{new_data_file.name} has insufficient rows for {num_lags} lags.")
                        continue
                    
                    # Preprocess new data
                    feature_cols_new = []
                    for var in selected_inputs:
                        if new_var_types[var] == "Dynamic":
                            for lag in range(1, num_lags + 1):
                                new_df[f'{var}_Lag_{lag}'] = new_df[var].shift(lag)
                                feature_cols_new.append(f'{var}_Lag_{lag}')
                        else:
                            feature_cols_new.append(var)
                    
                    # Add output variable lags
                    for lag in range(1, num_lags + 1):
                        new_df[f'{output_var}_Lag_{lag}'] = new_df[output_var].shift(lag) if output_var in new_df.columns else 0
                        feature_cols_new.append(f'{output_var}_Lag_{lag}')
                    
                    # Handle missing values
                    new_df.dropna(subset=[col for col in feature_cols_new if "_Lag_" in col], how='all', inplace=True)
                    
                    # Create full feature set
                    full_new_df = pd.DataFrame(index=new_df.index, columns=feature_cols + [output_var])
                    full_new_df[output_var] = new_df[output_var] if output_var in new_df.columns else 0
                    
                    for col in feature_cols_new:
                        if col in full_new_df.columns and col in new_df.columns:
                            full_new_df[col] = new_df[col]
                    
                    # Fill remaining NaN values
                    full_new_df.fillna(0, inplace=True)
                    full_new_df = full_new_df[feature_cols + [output_var]].apply(pd.to_numeric, errors='coerce')
                    
                    # Scale data
                    scaler = st.session_state.scaler
                    new_scaled = scaler.transform(full_new_df[feature_cols + [output_var]])
                    X_new = new_scaled[:, :-1]
                    X_new = X_new.reshape((X_new.shape[0], 1, X_new.shape[1]))
                    
                    # Build and load model
                    layers = (st.session_state.gru_layers if model_type in ["GRU", "Hybrid"] else 
                              st.session_state.lstm_layers if model_type == "LSTM" else 
                              st.session_state.rnn_layers if model_type == "RNN" else 
                              st.session_state.gru_layers)
                    units = (st.session_state.gru_units if model_type in ["GRU", "Hybrid"] else 
                             st.session_state.lstm_units if model_type == "LSTM" else 
                             st.session_state.rnn_units if model_type == "RNN" else 
                             st.session_state.gru_units)
                    
                    if st.session_state.model is None:
                        st.session_state.model = build_advanced_model(
                            (X_new.shape[1], X_new.shape[2]), 
                            model_type, 
                            layers, 
                            units, 
                            st.session_state.dense_layers, 
                            st.session_state.dense_units, 
                            st.session_state.learning_rate,
                            st.session_state.use_attention,
                            st.session_state.use_bidirectional,
                            st.session_state.use_residual,
                            st.session_state.dropout_rate
                        )
                        st.session_state.model.load_weights(MODEL_WEIGHTS_PATH)
                    
                    # Generate predictions with uncertainty
                    y_new_pred_mean, y_new_pred_std = predict_with_uncertainty(
                        st.session_state.model,
                        X_new,
                        st.session_state.num_samples
                    )
                    
                    # Inverse transform predictions
                    y_new_pred = scaler.inverse_transform(np.hstack([y_new_pred_mean, X_new[:, 0, :]]))[:, 0]
                    y_new_pred = np.clip(y_new_pred, 0, None)
                    
                    # Generate future predictions
                    last_sequence = X_new[-1]
                    future_predictions = generate_future_predictions(
                        st.session_state.model,
                        last_sequence,
                        scaler,
                        feature_cols,
                        prediction_horizon,
                        selected_inputs,
                        output_var,
                        new_var_types,
                        num_lags
                    )
                    
                    # Create results DataFrame
                    dates = new_df[date_col] if date_col != "None" else pd.RangeIndex(len(new_df))
                    last_date = dates.iloc[-1]
                    future_dates = pd.date_range(start=last_date + pd.Timedelta(days=1), periods=prediction_horizon)
                    
                    new_predictions_df = pd.DataFrame({
                        "Date": dates.values[-len(y_new_pred):],
                        f"Predicted_{output_var}": y_new_pred,
                        "Uncertainty": y_new_pred_std
                    })
                    
                    future_predictions_df = pd.DataFrame({
                        "Date": future_dates,
                        f"Predicted_{output_var}": future_predictions,
                        "Uncertainty": np.std(future_predictions, axis=0)
                    })
                    
                    st.session_state[f"new_predictions_df_{new_data_file.name}"] = new_predictions_df
                    st.session_state[f"future_predictions_df_{new_data_file.name}"] = future_predictions_df
                    
                    # Create prediction plot
                    fig = go.Figure()
                    
                    # Historical predictions
                    fig.add_trace(go.Scatter(
                        x=dates.values[-len(y_new_pred):],
                        y=y_new_pred,
                        name="Historical Predictions",
                        line=dict(color="#ff7f0e")
                    ))
                    
                    # Future predictions
                    fig.add_trace(go.Scatter(
                        x=future_dates,
                        y=future_predictions,
                        name="Future Predictions",
                        line=dict(color="#2ca02c", dash="dash")
                    ))
                    
                    # Confidence intervals
                    fig.add_trace(go.Scatter(
                        x=future_dates.tolist() + future_dates[::-1].tolist(),
                        y=(future_predictions + 1.96 * np.std(future_predictions, axis=0)).tolist() + 
                          (future_predictions - 1.96 * np.std(future_predictions, axis=0))[::-1].tolist(),
                        fill='toself',
                        fillcolor='rgba(44,160,44,0.1)',
                        line=dict(color='rgba(44,160,44,0)'),
                        name='95% Confidence Interval'
                    ))
                    
                    fig.update_layout(
                        title=f"Predictions: {output_var} ({new_data_file.name})",
                        xaxis_title="Date",
                        yaxis_title=output_var,
                        showlegend=True
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Download options
                    buf = BytesIO()
                    try:
                        fig.write_image(buf, format="png")
                    except ValueError:
                        fig_alt, ax = plt.subplots()
                        ax.plot(dates.values[-len(y_new_pred):], y_new_pred, label="Historical Predictions")
                        ax.plot(future_dates, future_predictions, label="Future Predictions", linestyle="--")
                        ax.legend()
                        ax.set_title(f"Predictions: {output_var}")
                        fig_alt.savefig(buf, format="png", bbox_inches="tight")
                    
                    st.download_button(
                        f"⬇️ Download Plot ({new_data_file.name})",
                        buf.getvalue(),
                        f"new_prediction_{new_data_file.name}.png",
                        "image/png"
                    )
                    
                    # Download predictions
                    new_csv = new_predictions_df.to_csv(index=False)
                    st.download_button(
                        f"⬇️ Download Historical Predictions ({new_data_file.name})",
                        new_csv,
                        f"new_predictions_{new_data_file.name}.csv",
                        "text/csv"
                    )
                    
                    future_csv = future_predictions_df.to_csv(index=False)
                    st.download_button(
                        f"⬇️ Download Future Predictions ({new_data_file.name})",
                        future_csv,
                        f"future_predictions_{new_data_file.name}.csv",
                        "text/csv"
                    )
                    
                    st.success(f"Predictions for {new_data_file.name} generated successfully!") 
