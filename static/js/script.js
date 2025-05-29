document.addEventListener('DOMContentLoaded', () => {
    const priceDisplay = document.getElementById('price-display');
    const refreshButton = document.getElementById('refresh-button');
    const symbol = 'EUR/USD'; // Define the forex symbol

    async function fetchForexPrice() {
        priceDisplay.innerHTML = '<p>Fetching latest price...</p>';
        try {
            const response = await fetch(`/price?symbol=${symbol}`); // Calls your Flask backend
            const data = await response.json();

            if (response.ok) { // Check if the HTTP response was successful (status 200)
                let timestampText = '';
                if (data.timestamp) {
                    // Correctly format timestamp for display
                    timestampText = ` as of ${new Date(data.timestamp).toLocaleString()}`;
                }
                // CORRECTED LINE: Ensure data.price is displayed correctly with template literals
                priceDisplay.innerHTML = `<p><strong>${data.symbol}:</strong> ${data.price}${timestampText}</p>`;
            } else {
                // Handle API errors based on the Flask jsonify response
                priceDisplay.innerHTML = `<p>Error: ${data.message || 'Could not fetch price.'}</p>`;
                console.error('API Error:', data.message);
            }
        } catch (error) {
            console.error('Error fetching forex price:', error);
            priceDisplay.innerHTML = '<p>Error fetching data. Please check server.</p>';
        }
    }

    // Fetch price on page load
    fetchForexPrice();

    // Fetch price when refresh button is clicked
    refreshButton.addEventListener('click', fetchForexPrice);

    // Optional: Auto-refresh every 30 seconds
    // setInterval(fetchForexPrice, 30000);
});