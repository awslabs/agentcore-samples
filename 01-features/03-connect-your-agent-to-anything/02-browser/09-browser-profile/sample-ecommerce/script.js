const products = [
    { id: 1, name: "Classic White T-Shirt", price: 19.99, image: "images/tshirt-1.png", thumb: "images/tshirt-1-thumb.png" },
    { id: 2, name: "Black Cotton T-Shirt", price: 22.99, image: "images/tshirt-2.png", thumb: "images/tshirt-2-thumb.png" },
    { id: 3, name: "Blue Graphic T-Shirt", price: 24.99, image: "images/tshirt-3.png", thumb: "images/tshirt-3-thumb.png" },
    { id: 4, name: "Red V-Neck T-Shirt", price: 21.99, image: "images/tshirt-4.png", thumb: "images/tshirt-4-thumb.png" },
    { id: 5, name: "Green Polo T-Shirt", price: 29.99, image: "images/tshirt-5.png", thumb: "images/tshirt-5-thumb.png" },
    { id: 6, name: "Gray Striped T-Shirt", price: 26.99, image: "images/tshirt-6.png", thumb: "images/tshirt-6-thumb.png" }
];

let cart = JSON.parse(localStorage.getItem('cart')) || [];

function renderProducts() {
    const grid = document.getElementById('productGrid');
    grid.innerHTML = products.map(p => `
        <div class="product">
            <img src="${p.image}" alt="${p.name}">
            <h3>${p.name}</h3>
            <p>$${p.price}</p>
            <button onclick="addToCart(${p.id})">Add to Cart</button>
        </div>
    `).join('');
}

function addToCart(productId) {
    const product = products.find(p => p.id === productId);
    cart.push(product);
    saveCart();
    updateCartCount();
}

function removeFromCart(index) {
    cart.splice(index, 1);
    saveCart();
    renderCart();
    updateCartCount();
}

function renderCart() {
    const cartItems = document.getElementById('cartItems');
    if (cart.length === 0) {
        cartItems.innerHTML = '<p>Your cart is empty</p>';
    } else {
        cartItems.innerHTML = cart.map((item, index) => `
            <div class="cart-item">
                <img src="${item.thumb}" alt="${item.name}">
                <div>
                    <strong>${item.name}</strong><br>
                    $${item.price}
                </div>
                <button onclick="removeFromCart(${index})">Remove</button>
            </div>
        `).join('') + `<h3>Total: $${cart.reduce((sum, item) => sum + item.price, 0).toFixed(2)}</h3>`;
    }
}

function saveCart() {
    localStorage.setItem('cart', JSON.stringify(cart));
}

function updateCartCount() {
    document.getElementById('cartCount').textContent = cart.length;
}

document.getElementById('viewCart').addEventListener('click', () => {
    document.getElementById('products').style.display = 'none';
    document.getElementById('cart').style.display = 'block';
    window.location.hash = '#cart';
    renderCart();
});

document.getElementById('backToProducts').addEventListener('click', () => {
    document.getElementById('products').style.display = 'block';
    document.getElementById('cart').style.display = 'none';
    window.location.hash = '#home';
});

renderProducts();
updateCartCount();
