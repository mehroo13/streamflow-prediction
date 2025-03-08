import streamlit as st
import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Input, Dense, GRU, LSTM, SimpleRNN, Dropout, Bidirectional, Layer, concatenate
from tensorflow.keras.models import Model
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau, ModelCheckpoint, TensorBoard
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
import warnings
import optuna
from scipy import stats

# Define constants
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

# Set page config first
st.set_page_config(page_title="Wateran", page_icon="🌊", layout="wide")

# Suppress all warnings
warnings.filterwarnings('ignore')
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

# Theme and styling
st.markdown("""
    <style>
    .main {
        padding: 0rem 1rem;
    }
    .ad-container {
        display: flex;
        justify-content: center;
        align-items: center;
        margin: 1rem 0;
        min-height: 90px;
    }
    </style>
""", unsafe_allow_html=True)

# AdMob initialization script
st.components.v1.html("""
    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-app-pub-2264561932019289"
     crossorigin="anonymous"></script>
""", height=0)

# Top ad container
st.components.v1.html("""
    <div class="ad-container">
        <ins class="adsbygoogle"
            style="display:inline-block;width:728px;height:90px"
            data-ad-client="ca-app-pub-2264561932019289"
            data-ad-slot="9782119699">
        </ins>
        <script>
            (adsbygoogle = window.adsbygoogle || []).push({});
        </script>
    </div>
""", height=110)

# Title and description
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
if 'use_attention' not in st.session_state:
    st.session_state.use_attention = False
if 'use_bidirectional' not in st.session_state:
    st.session_state.use_bidirectional = False
if 'use_residual' not in st.session_state:
    st.session_state.use_residual = False
if 'dropout_rate' not in st.session_state:
    st.session_state.dropout_rate = 0.2
if 'prediction_horizon' not in st.session_state:
    st.session_state.prediction_horizon = DEFAULT_PREDICTION_HORIZON
if 'num_samples' not in st.session_state:
    st.session_state.num_samples = 100
if 'gru_layers' not in st.session_state:
    st.session_state.gru_layers = 1
if 'lstm_layers' not in st.session_state:
    st.session_state.lstm_layers = 1
if 'rnn_layers' not in st.session_state:
    st.session_state.rnn_layers = 1
if 'dense_layers' not in st.session_state:
    st.session_state.dense_layers = 1
if 'gru_units' not in st.session_state:
    st.session_state.gru_units = [DEFAULT_GRU_UNITS]
if 'lstm_units' not in st.session_state:
    st.session_state.lstm_units = [DEFAULT_LSTM_UNITS]
if 'rnn_units' not in st.session_state:
    st.session_state.rnn_units = [DEFAULT_RNN_UNITS]
if 'dense_units' not in st.session_state:
    st.session_state.dense_units = [DEFAULT_DENSE_UNITS]
if 'learning_rate' not in st.session_state:
    st.session_state.learning_rate = DEFAULT_LEARNING_RATE
if 'hybrid_models' not in st.session_state:
    st.session_state.hybrid_models = ["GRU"]
if 'metrics' not in st.session_state:
    st.session_state.metrics = None
if 'train_results_df' not in st.session_state:
    st.session_state.train_results_df = None
if 'test_results_df' not in st.session_state:
    st.session_state.test_results_df = None
if 'fig' not in st.session_state:
    st.session_state.fig = None
if 'model_plot' not in st.session_state:
    st.session_state.model_plot = None
if 'scaler' not in st.session_state:
    st.session_state.scaler = None
if 'new_predictions_df' not in st.session_state:
    st.session_state.new_predictions_df = None
if 'new_fig' not in st.session_state:
    st.session_state.new_fig = None
if 'selected_inputs' not in st.session_state:
    st.session_state.selected_inputs = None
if 'new_date_col' not in st.session_state:
    st.session_state.new_date_col = None
if 'selected_metrics' not in st.session_state:
    st.session_state.selected_metrics = None
if 'new_var_types' not in st.session_state:
    st.session_state.new_var_types = None
if 'cv_metrics' not in st.session_state:
    st.session_state.cv_metrics = None
if 'X_train' not in st.session_state:
    st.session_state.X_train = None
if 'y_train' not in st.session_state:
    st.session_state.y_train = None
if 'X_test' not in st.session_state:
    st.session_state.X_test = None
if 'y_test' not in st.session_state:
    st.session_state.y_test = None
if 'model' not in st.session_state:
    st.session_state.model = None

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
    
    epochs = st.slider("Epochs", 1, 1500, DEFAULT_EPOCHS, step=1, key="epochs")
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
            st.markdown("### PINN Configuration")
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
                            
                            # Ensure data has correct shape
                            if len(X_train_opt.shape) == 2:
                                X_train_opt = X_train_opt.reshape((X_train_opt.shape[0], 1, X_train_opt.shape[1]))
                            if len(y_train_opt.shape) == 1:
                                y_train_opt = y_train_opt.reshape(-1, 1)
                            if len(X_val.shape) == 2:
                                X_val = X_val.reshape((X_val.shape[0], 1, X_val.shape[1]))
                            if len(y_val.shape) == 1:
                                y_val = y_val.reshape(-1, 1)
                            
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
                except:
                    # Silently continue with basic features
                    pass
            
            # Scale data
            scaler = MinMaxScaler()  # Create a new scaler for CV
            scaled = scaler.fit_transform(processed_df[feature_cols + [st.session_state.output_var]])
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
                
                # Reshape predictions and validation data
                y_val_pred_mean = y_val_pred_mean.reshape(-1, 1)
                X_val_features = X_val[:, 0, :]
                
                # Ensure arrays have matching first dimensions
                min_len = min(len(y_val_pred_mean), len(X_val_features))
                y_val_pred_mean = y_val_pred_mean[:min_len]
                X_val_features = X_val_features[:min_len]
                y_val = y_val[:min_len]
                
                # Stack arrays and inverse transform
                stacked_pred = np.hstack([y_val_pred_mean, X_val_features])
                stacked_actual = np.hstack([y_val, X_val_features])
                
                y_val_pred = scaler.inverse_transform(stacked_pred)[:, 0]
                y_val_actual = scaler.inverse_transform(stacked_actual)[:, 0]
                
                # Calculate metrics
                for metric in st.session_state.selected_metrics:
                    cv_metrics[metric].append(all_metrics_dict[metric](y_val_actual, y_val_pred))
                
                # Clean up
                tf.keras.backend.clear_session()
            
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
                    
                    # Reshape predictions to match dimensions
                    y_new_pred_mean = y_new_pred_mean.reshape(-1, 1)  # Reshape to 2D array
                    X_new_2d = X_new[:, 0, :].reshape(y_new_pred_mean.shape[0], -1)  # Ensure matching dimensions
                    
                    # Ensure arrays have matching first dimensions
                    min_len = min(len(y_new_pred_mean), len(X_new_2d))
                    y_new_pred_mean = y_new_pred_mean[:min_len]
                    X_new_2d = X_new_2d[:min_len]
                    
                    # Inverse transform predictions
                    y_new_pred = scaler.inverse_transform(np.hstack([y_new_pred_mean, X_new_2d]))[:, 0]
                    y_new_pred = np.clip(y_new_pred, 0, None)
                    
                    # Create results DataFrame
                    dates = new_df[date_col] if date_col != "None" else pd.RangeIndex(len(new_df))
                    
                    # Create predictions DataFrame with proper date handling
                    predictions_df = pd.DataFrame({
                        "Date": dates,
                        "Actual_Discharge": new_df[output_var] if output_var in new_df.columns else None,
                        "Predicted_Discharge": y_new_pred,
                        "Uncertainty": y_new_pred_std[:min_len]
                    })
                    
                    # Create enhanced plot
                    fig = go.Figure()
                    
                    # Plot actual discharge if available
                    if output_var in new_df.columns:
                        fig.add_trace(go.Scatter(
                            x=dates,
                            y=new_df[output_var],
                            name="Actual Discharge",
                            line=dict(color='blue', width=2),
                            hovertemplate='<b>Date</b>: %{x}<br><b>Actual Discharge</b>: %{y:.2f} m³/s<br>'
                        ))
                    
                    # Plot predicted discharge
                    fig.add_trace(go.Scatter(
                        x=dates[-len(y_new_pred):],
                        y=y_new_pred,
                        name="Predicted Discharge",
                        line=dict(color='red', width=2),
                        hovertemplate='<b>Date</b>: %{x}<br><b>Predicted Discharge</b>: %{y:.2f} m³/s<br>'
                    ))
                    
                    # Add confidence interval
                    fig.add_trace(go.Scatter(
                        x=dates[-len(y_new_pred):].tolist() + dates[-len(y_new_pred):][::-1].tolist(),
                        y=(y_new_pred + 1.96 * y_new_pred_std[:min_len]).tolist() + 
                          (y_new_pred - 1.96 * y_new_pred_std[:min_len])[::-1].tolist(),
                        fill='toself',
                        fillcolor='rgba(255,0,0,0.2)',
                        line=dict(color='rgba(255,0,0,0)'),
                        name='95% Confidence Interval',
                        showlegend=True
                    ))
                    
                    # Update layout
                    fig.update_layout(
                        title=dict(
                            text=f"Discharge Analysis - {new_data_file.name}",
                            font=dict(size=24)
                        ),
                        xaxis=dict(
                            title="Date",
                            showgrid=True,
                            gridcolor='rgba(211, 211, 211, 0.4)',
                            showline=True
                        ),
                        yaxis=dict(
                            title="Discharge (m³/s)",
                            showgrid=True,
                            gridcolor='rgba(211, 211, 211, 0.4)',
                            showline=True,
                            rangemode='nonnegative'
                        ),
                        plot_bgcolor='white',
                        height=700,
                        hovermode='x unified'
                    )
                    
                    # Display the plot
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Create download buttons
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # Plot download
                        try:
                            img_bytes = BytesIO()
                            fig.write_image(img_bytes, format='png', width=1920, height=1080, scale=2)
                            img_bytes.seek(0)
                            
                            st.download_button(
                                "⬇️ Download Plot (PNG)",
                                img_bytes.getvalue(),
                                f"discharge_analysis_{new_data_file.name}.png",
                                mime="image/png"
                            )
                        except Exception as e:
                            # Silently switch to alternative method without showing warning
                            plt.figure(figsize=(16, 9), dpi=300)
                            if output_var in new_df.columns:
                                plt.plot(dates, new_df[output_var], 'b-', linewidth=2, label='Actual')
                            plt.plot(dates[-len(y_new_pred):], y_new_pred, 'r-', linewidth=2, label='Predicted')
                            plt.fill_between(dates[-len(y_new_pred):],
                                           y_new_pred - 1.96 * y_new_pred_std[:min_len],
                                           y_new_pred + 1.96 * y_new_pred_std[:min_len],
                                           color='red', alpha=0.2, label='95% CI')
                            plt.title(f"Discharge Analysis - {new_data_file.name}")
                            plt.xlabel("Date")
                            plt.ylabel("Discharge (m³/s)")
                            plt.grid(True, alpha=0.3)
                            plt.legend()
                            plt.xticks(rotation=45)
                            
                            img_bytes = BytesIO()
                            plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
                            img_bytes.seek(0)
                            plt.close()
                            
                            st.download_button(
                                "⬇️ Download Plot (PNG)",
                                img_bytes.getvalue(),
                                f"discharge_analysis_{new_data_file.name}.png",
                                mime="image/png"
                            )
                    
                    with col2:
                        # Data download
                        csv = predictions_df.to_csv(index=False)
                        st.download_button(
                            "⬇️ Download Predictions (CSV)",
                            csv,
                            f"discharge_predictions_{new_data_file.name}.csv",
                            "text/csv"
                        )
                    
                    # Display predictions table
                    st.markdown("### Prediction Results")
                    st.dataframe(predictions_df, use_container_width=True)
                    
                    st.success(f"Analysis completed successfully for {new_data_file.name}!")

# Bottom ad container at the end
st.components.v1.html("""
    <div class="ad-container">
        <ins class="adsbygoogle"
            style="display:inline-block;width:728px;height:90px"
            data-ad-client="ca-app-pub-2264561932019289"
            data-ad-slot="3656766879">
        </ins>
        <script>
            (adsbygoogle = window.adsbygoogle || []).push({});
        </script>
    </div>
""", height=110)
