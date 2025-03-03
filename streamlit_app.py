import streamlit as st
import pandas as pd
import numpy as np
import tensorflow as tf
import os
import tempfile
import matplotlib.pyplot as plt
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from io import BytesIO
from tensorflow.keras.utils import plot_model

# -------------------- Model Parameters --------------------
DEFAULT_GRU_UNITS = 64
DEFAULT_DENSE_UNITS = 32
DEFAULT_LEARNING_RATE = 0.001
DEFAULT_EPOCHS = 50
DEFAULT_BATCH_SIZE = 16
DEFAULT_TRAIN_SPLIT = 80
DEFAULT_NUM_LAGS = 3
MODEL_WEIGHTS_PATH = os.path.join(tempfile.gettempdir(), "gru_model_weights.weights.h5")
MODEL_PLOT_PATH = os.path.join(tempfile.gettempdir(), "gru_model_plot.png")

# -------------------- Metric Functions --------------------
# (Metric functions remain unchanged: nse, kge, pbias, peak_flow_error, high_flow_bias, low_flow_bias, volume_error)

# -------------------- Custom Callback for Epoch Tracking --------------------
class StreamlitProgressCallback(tf.keras.callbacks.Callback):
    def __init__(self, total_epochs, progress_placeholder):
        super().__init__()
        self.total_epochs = total_epochs
        self.progress_placeholder = progress_placeholder
        self.current_epoch = 0

    def on_epoch_end(self, epoch, logs=None):
        self.current_epoch = epoch + 1
        progress = self.current_epoch / self.total_epochs
        self.progress_placeholder.progress(min(progress, 1.0))
        self.progress_placeholder.text(f"Epoch {self.current_epoch}/{self.total_epochs} completed")

# -------------------- GRU Model Definition --------------------
def build_gru_model(input_shape, gru_layers, dense_layers, gru_units, dense_units, learning_rate):
    model = tf.keras.Sequential()
    for i in range(gru_layers):
        if i == 0:
            model.add(tf.keras.layers.GRU(gru_units[i], return_sequences=(i < gru_layers - 1), input_shape=input_shape))
        else:
            model.add(tf.keras.layers.GRU(gru_units[i], return_sequences=(i < gru_layers - 1)))
        model.add(tf.keras.layers.Dropout(0.2))
    for units in dense_units[:dense_layers]:
        model.add(tf.keras.layers.Dense(units, activation='relu'))
    model.add(tf.keras.layers.Dense(1))
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate), loss='mse')
    return model

# -------------------- Streamlit UI --------------------
st.set_page_config(page_title="Wateran", page_icon="🌊", layout="wide")

# Branding
with st.sidebar:
    st.image("path_to_your_logo.png", width=150)  # Replace with your logo path
    st.title("🌊 Wateran")
    st.markdown("**Predict time series with GRU**")

# Sidebar for Data Upload and Variable Selection
with st.sidebar.form(key='data_form'):
    st.subheader("📥 Data Input")
    uploaded_file = st.file_uploader("Upload Training Data (Excel)", type=["xlsx"], help="Upload an Excel file with your time series data.")
    
    if uploaded_file:
        df = pd.read_excel(uploaded_file)
        st.write("Preview:", df.head(5))
        
        datetime_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col]) or "date" in col.lower()]
        date_col = st.selectbox("Date Column (optional)", ["None"] + datetime_cols, index=0, help="Select a column with dates, or use index if none.") if datetime_cols else "None"
        if date_col != "None":
            df[date_col] = pd.to_datetime(df[date_col])
            df = df.sort_values(date_col)
        
        numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col]) and (date_col is None or col != date_col)]
        if len(numeric_cols) < 2:
            st.error("Need at least two numeric columns.")
            st.stop()
        
        output_var = st.selectbox("🎯 Output Variable", numeric_cols, help="Choose the variable to predict.")
        input_vars = st.multiselect("🔧 Input Variables", [col for col in numeric_cols if col != output_var], default=[numeric_cols[0]], help="Select variables to use as inputs.")
        
        if st.form_submit_button("Submit Data"):
            st.session_state.df = df
            st.session_state.date_col = date_col
            st.session_state.output_var = output_var
            st.session_state.input_vars = input_vars
            st.session_state.var_types = {var: "Dynamic" for var in input_vars}  # Default to Dynamic

# Sidebar for Model Configuration
with st.sidebar.form(key='config_form'):
    st.subheader("⚙️ Model Configuration")
    epochs = st.slider("Epochs", 1, 1500, DEFAULT_EPOCHS, step=10, help="Number of training iterations.")
    batch_size = st.slider("Batch Size", 8, 128, DEFAULT_BATCH_SIZE, step=8, help="Number of samples per gradient update.")
    train_split = st.slider("Training Data %", 50, 90, DEFAULT_TRAIN_SPLIT, help="Percentage of data for training.") / 100
    num_lags = st.number_input("Number of Lags", 1, 10, DEFAULT_NUM_LAGS, step=1, help="Time steps to look back.")
    
    with st.expander("Advanced Architecture"):
        gru_layers = st.number_input("GRU Layers", 1, 5, 1, help="Number of GRU layers.")
        gru_units = [st.number_input(f"GRU Layer {i+1} Units", 8, 512, DEFAULT_GRU_UNITS, step=8) for i in range(gru_layers)]
        dense_layers = st.number_input("Dense Layers", 1, 5, 1, help="Number of dense layers.")
        dense_units = [st.number_input(f"Dense Layer {i+1} Units", 8, 512, DEFAULT_DENSE_UNITS, step=8) for i in range(dense_layers)]
        learning_rate = st.number_input("Learning Rate", 0.00001, 0.1, DEFAULT_LEARNING_RATE, format="%.5f", help="Step size for optimization.")
    
    selected_metrics = st.multiselect("Evaluation Metrics", ["RMSE", "MAE", "R²", "NSE", "KGE", "PBIAS", "Peak Flow Error", "High Flow Bias", "Low Flow Bias", "Volume Error"], default=["RMSE", "MAE", "R²"], help="Metrics to evaluate model performance.")
    
    if st.form_submit_button("Apply Settings"):
        st.session_state.gru_layers = gru_layers
        st.session_state.dense_layers = dense_layers
        st.session_state.gru_units = gru_units
        st.session_state.dense_units = dense_units
        st.session_state.learning_rate = learning_rate
        st.session_state.selected_metrics = selected_metrics
        st.session_state.num_lags = num_lags

# Main Area
st.header("Model Training and Results")

if 'df' in st.session_state:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 Train Model"):
            # Training logic (unchanged, adapted to use session state variables)
            df = st.session_state.df.copy()
            feature_cols = []
            for var in st.session_state.input_vars:
                if st.session_state.var_types[var] == "Dynamic":
                    for lag in range(1, st.session_state.num_lags + 1):
                        df[f'{var}_Lag_{lag}'] = df[var].shift(lag)
                        feature_cols.append(f'{var}_Lag_{lag}')
                else:
                    df[var] = df[var].fillna(df[var].median() if df[var].isnull().sum() <= len(df) * 0.9 else 0)
                    feature_cols.append(var)
            for lag in range(1, st.session_state.num_lags + 1):
                df[f'{st.session_state.output_var}_Lag_{lag}'] = df[st.session_state.output_var].shift(lag)
                feature_cols.append(f'{st.session_state.output_var}_Lag_{lag}')
            
            df = df.dropna(subset=[col for col in feature_cols if "_Lag_" in col], how='all')
            df[feature_cols] = df[feature_cols].fillna(0)
            
            train_size = int(len(df) * train_split)
            train_df, test_df = df[:train_size], df[train_size:]
            scaler = MinMaxScaler()
            train_scaled = scaler.fit_transform(train_df[feature_cols + [st.session_state.output_var]])
            test_scaled = scaler.transform(test_df[feature_cols + [st.session_state.output_var]])
            st.session_state.scaler = scaler
            st.session_state.feature_cols = feature_cols
            
            X_train = train_scaled[:, :-1].reshape((train_scaled.shape[0], 1, train_scaled.shape[1] - 1))
            y_train = train_scaled[:, -1]
            X_test = test_scaled[:, :-1].reshape((test_scaled.shape[0], 1, test_scaled.shape[1] - 1))
            
            model = build_gru_model((X_train.shape[1], X_train.shape[2]), gru_layers, dense_layers, gru_units, dense_units, learning_rate)
            with st.spinner("Training..."):
                progress_placeholder = st.empty()
                model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size, verbose=0, callbacks=[StreamlitProgressCallback(epochs, progress_placeholder)])
                model.save_weights(MODEL_WEIGHTS_PATH)
            st.success("Model trained!")

    with col2:
        if st.button("🔍 Test Model") and os.path.exists(MODEL_WEIGHTS_PATH):
            # Testing logic (unchanged, adapted)
            df = st.session_state.df.copy()
            for var in st.session_state.input_vars:
                if st.session_state.var_types[var] == "Dynamic":
                    for lag in range(1, st.session_state.num_lags + 1):
                        df[f'{var}_Lag_{lag}'] = df[var].shift(lag)
                else:
                    df[var] = df[var].fillna(df[var].median() if df[var].isnull().sum() <= len(df) * 0.9 else 0)
            for lag in range(1, st.session_state.num_lags + 1):
                df[f'{st.session_state.output_var}_Lag_{lag}'] = df[st.session_state.output_var].shift(lag)
            
            df = df.dropna(subset=[col for col in st.session_state.feature_cols if "_Lag_" in col], how='all')
            df[st.session_state.feature_cols] = df[st.session_state.feature_cols].fillna(0)
            
            train_size = int(len(df) * train_split)
            train_df, test_df = df[:train_size], df[train_size:]
            train_scaled = st.session_state.scaler.transform(train_df[st.session_state.feature_cols + [st.session_state.output_var]])
            test_scaled = st.session_state.scaler.transform(test_df[st.session_state.feature_cols + [st.session_state.output_var]])
            
            X_train = train_scaled[:, :-1].reshape((train_scaled.shape[0], 1, train_scaled.shape[1] - 1))
            y_train = train_scaled[:, -1]
            X_test = test_scaled[:, :-1].reshape((test_scaled.shape[0], 1, test_scaled.shape[1] - 1))
            y_test = test_scaled[:, -1]
            
            model = build_gru_model((X_train.shape[1], X_train.shape[2]), gru_layers, dense_layers, gru_units, dense_units, learning_rate)
            model.load_weights(MODEL_WEIGHTS_PATH)
            y_train_pred = model.predict(X_train)
            y_test_pred = model.predict(X_test)
            y_train_pred = st.session_state.scaler.inverse_transform(np.hstack([y_train_pred, X_train[:, 0, :]]))[:, 0]
            y_test_pred = st.session_state.scaler.inverse_transform(np.hstack([y_test_pred, X_test[:, 0, :]]))[:, 0]
            y_train_actual = st.session_state.scaler.inverse_transform(np.hstack([y_train.reshape(-1, 1), X_train[:, 0, :]]))[:, 0]
            y_test_actual = st.session_state.scaler.inverse_transform(np.hstack([y_test.reshape(-1, 1), X_test[:, 0, :]]))[:, 0]
            
            metrics = {m: {"Training": globals()[m.lower().replace(" ", "_")](y_train_actual, y_train_pred) if m in ["NSE", "KGE", "PBIAS", "Peak Flow Error", "High Flow Bias", "Low Flow Bias", "Volume Error"] else globals()[{"RMSE": "np.sqrt(mean_squared_error)", "MAE": "mean_absolute_error", "R²": "r2_score"}[m]](y_train_actual, y_train_pred),
                           "Testing": globals()[m.lower().replace(" ", "_")](y_test_actual, y_test_pred) if m in ["NSE", "KGE", "PBIAS", "Peak Flow Error", "High Flow Bias", "Low Flow Bias", "Volume Error"] else globals()[{"RMSE": "np.sqrt(mean_squared_error)", "MAE": "mean_absolute_error", "R²": "r2_score"}[m]](y_test_actual, y_test_pred)}
                       for m in st.session_state.selected_metrics}
            st.session_state.metrics = metrics
            
            dates = df[st.session_state.date_col] if st.session_state.date_col != "None" else pd.RangeIndex(len(df))
            train_dates, test_dates = dates[:train_size], dates[train_size:]
            st.session_state.train_results_df = pd.DataFrame({"Date": train_dates[:len(y_train_actual)], f"Actual_{st.session_state.output_var}": y_train_actual, f"Predicted_{st.session_state.output_var}": y_train_pred})
            st.session_state.test_results_df = pd.DataFrame({"Date": test_dates[:len(y_test_actual)], f"Actual_{st.session_state.output_var}": y_test_actual, f"Predicted_{st.session_state.output_var}": y_test_pred})
            
            fig, ax = plt.subplots(2, 1, figsize=(12, 8))
            ax[0].plot(train_dates[:len(y_train_actual)], y_train_actual, label="Actual", color="#1f77b4")
            ax[0].plot(train_dates[:len(y_train_pred)], y_train_pred, label="Predicted", color="#ff7f0e", linestyle="--")
            ax[0].set_title(f"Training: {st.session_state.output_var}")
            ax[0].legend()
            ax[1].plot(test_dates[:len(y_test_actual)], y_test_actual, label="Actual", color="#1f77b4")
            ax[1].plot(test_dates[:len(y_test_pred)], y_test_pred, label="Predicted", color="#ff7f0e", linestyle="--")
            ax[1].set_title(f"Testing: {st.session_state.output_var}")
            ax[1].legend()
            plt.tight_layout()
            st.session_state.fig = fig
            st.success("Model tested!")

# Results
with st.expander("📊 Results", expanded=True):
    if st.session_state.get('metrics'):
        st.subheader("Metrics")
        metrics_df = pd.DataFrame(st.session_state.metrics).T
        st.table(metrics_df)
    if st.session_state.get('fig'):
        st.pyplot(st.session_state.fig)
        buf = BytesIO()
        st.session_state.fig.savefig(buf, format="png")
        st.download_button("Download Plot", buf.getvalue(), "plot.png")

# New Predictions
if os.path.exists(MODEL_WEIGHTS_PATH):
    with st.expander("🔮 New Predictions"):
        new_data_file = st.file_uploader("Upload New Data (Excel)", type=["xlsx"])
        if new_data_file:
            new_df = pd.read_excel(new_data_file)
            st.write(new_df.head())
            # Prediction logic (simplified for brevity, adapt as needed)
            if st.button("Predict"):
                # Use trained model for predictions
                pass
